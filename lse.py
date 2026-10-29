"""
Segunda fuente OPCIONAL de fundamentales: LSE (London Strategic Edge).

yfinance sigue siendo la fuente principal. Este módulo solo cruza los márgenes de los
filtros 1 (rentabilidad), 3 (moat) y 4 (FCF) con los estados anuales de LSE y, si difieren
en más de 3 puntos porcentuales, devuelve una nota "LSE difiere de yfinance". NO cambia
ningún veredicto PASS/WARN/FAIL.

Activación: la clave se lee SOLO de la variable de entorno LSE_KEY. Sin ella el módulo no
hace nada y el screener funciona exactamente igual que antes.

    $env:LSE_KEY = "<tu clave>"      # nunca en el código, ejemplos, README ni commits

Límites del plan gratis: 60 llamadas/min y UNA descarga a la vez, así que todo va en serie
(con un candado) y se respeta Retry-After ante un 429. Los datos descargados se guardan en
.cache_lse/ (ignorada por git): no se versionan datos de LSE.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

API_BASE = "https://download.londonstrategicedge.com/v3"
ENV_VAR = "LSE_KEY"
CACHE_DIR = Path(__file__).with_name(".cache_lse")
CACHE_TTL_DAYS = 90          # los años cerrados no cambian; un año nuevo aparece cada trimestre
FROM_DATE = "2016-01-01"     # ~10 años: reduce el peso (~2 MB por ticker con 1,000 filas)
MIN_INTERVAL = 1.1           # s entre llamadas: por debajo de 60/min
MAX_RETRIES = 3              # reintentos ante 429
DEFAULT_RETRY_AFTER = 5.0
MAX_RETRY_AFTER = 120.0
TIMEOUT = 120
DIVERGENCE_PP = 3.0          # puntos porcentuales
MATCH_DAYS = 60              # tolerancia al emparejar fechas de cierre fiscal con yfinance
CASHFLOW_MIN_YEAR = 2011     # antes de este año los ceros de flujo de caja son datos faltantes

_STATUS_MSG = {
    401: "LSE rechazó la clave (401): revisa LSE_KEY",
    403: "sin acceso a este dato con tu plan de LSE (403)",
    404: "ticker no disponible en LSE (404)",
    429: "límite de peticiones de LSE agotado (429)",
}

_lock = threading.Lock()     # una descarga a la vez
_last_call = 0.0
_blocked = ""                # tras un 401 no se vuelve a llamar en esta ejecución


class LseError(Exception):
    """Error de LSE. `status` es el código HTTP (0 = sin conexión). Nunca incluye la clave."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def api_key() -> str | None:
    key = os.environ.get(ENV_VAR, "").strip()
    return key or None


def available() -> bool:
    return api_key() is not None and not _blocked


def reset_state() -> None:
    """Solo para pruebas."""
    global _last_call, _blocked
    _last_call = 0.0
    _blocked = ""


# ----------------------------------------------------------------------------
# Descarga (serie, con límite de velocidad y reintento ante 429)
# ----------------------------------------------------------------------------
def _retry_after(err: urllib.error.HTTPError) -> float:
    raw = (err.headers.get("Retry-After") if err.headers else None) or ""
    try:
        return max(0.0, min(float(raw), MAX_RETRY_AFTER))
    except ValueError:
        return DEFAULT_RETRY_AFTER


def _download(symbol: str, key: str) -> str:
    global _last_call
    qs = urllib.parse.urlencode(
        {"dataset": "financial_reports", "symbol": symbol, "from": FROM_DATE, "format": "csv"}
    )
    url = f"{API_BASE}/reference/export?{qs}"
    with _lock:
        for attempt in range(MAX_RETRIES + 1):
            wait = MIN_INTERVAL - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers={"X-API-Key": key, "Accept": "text/csv"})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    body = r.read()
                _last_call = time.time()
                return body.decode("utf-8-sig")
            except urllib.error.HTTPError as e:
                _last_call = time.time()
                if e.code == 429 and attempt < MAX_RETRIES:
                    time.sleep(_retry_after(e))
                    continue
                raise LseError(e.code, _STATUS_MSG.get(e.code, f"error HTTP {e.code} de LSE")) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                _last_call = time.time()
                raise LseError(0, "sin conexión con LSE") from None
    raise LseError(429, _STATUS_MSG[429])  # no debería llegar aquí


def _symbol_variants(ticker: str) -> list[str]:
    t = ticker.strip().upper()
    return [t] + ([t.replace("-", ".")] if "-" in t else [])


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / (re.sub(r"[^A-Za-z0-9._-]", "_", symbol) + ".csv")


def _has_rows(text: str) -> bool:
    return len(text.strip().splitlines()) > 1


def load_csv(ticker: str, key: str) -> str:
    """CSV de financial_reports del ticker, desde caché (90 días) o descargado."""
    variants = _symbol_variants(ticker)
    for sym in variants:
        path = _cache_path(sym)
        if path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_DAYS * 86400:
            return path.read_text(encoding="utf-8")
    last: LseError | None = None
    for sym in variants:
        try:
            text = _download(sym, key)
        except LseError as e:
            last = e
            if e.status == 404:
                continue
            raise
        if not _has_rows(text):
            last = LseError(404, _STATUS_MSG[404])
            continue
        CACHE_DIR.mkdir(exist_ok=True)
        _cache_path(sym).write_text(text, encoding="utf-8")
        return text
    raise last or LseError(404, _STATUS_MSG[404])


# ----------------------------------------------------------------------------
# Parseo: solo period == "FY"
# ----------------------------------------------------------------------------
@dataclass
class FY:
    end: date
    currency: str = ""
    income: dict = field(default_factory=dict)
    cashflow: dict = field(default_factory=dict)
    balance: dict = field(default_factory=dict)


def _f(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_annual(text: str) -> dict[date, FY]:
    """Estados anuales por fecha de cierre. Si hay reexpresiones, gana el filing más reciente."""
    kinds = {"income", "cashflow", "balance"}
    chosen: dict[tuple[date, str], tuple[str, dict, str]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            if row["period"] != "FY" or row["report_type"] not in kinds:
                continue
            end = datetime.strptime(row["date"][:10], "%Y-%m-%d").date()
            data = json.loads(row["data"])
        except (KeyError, ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        filed = row.get("filing_date") or row.get("accepted_date") or ""
        k = (end, row["report_type"])
        if k not in chosen or filed >= chosen[k][0]:
            chosen[k] = (filed, data, row.get("reported_currency") or "")

    out: dict[date, FY] = {}
    for (end, kind), (_filed, data, cur) in chosen.items():
        fy = out.setdefault(end, FY(end=end))
        if cur and not fy.currency:
            fy.currency = cur
        vals = {k: _f(v) for k, v in data.items()}
        if kind == "cashflow" and end.year < CASHFLOW_MIN_YEAR:
            vals = {k: (None if v == 0 else v) for k, v in vals.items()}  # ceros = faltantes
        setattr(fy, kind, vals)
    return out


def annual_reports(ticker: str, key: str) -> dict[date, FY]:
    annual = parse_annual(load_csv(ticker, key))
    if not annual:
        raise LseError(404, "LSE no trae años fiscales (FY) para este ticker")
    return annual


# ----------------------------------------------------------------------------
# Métricas
# ----------------------------------------------------------------------------
def op_margin(fy: FY) -> float | None:
    rev, oi = fy.income.get("revenue"), fy.income.get("operatingIncome")
    if not rev or oi is None:
        return None
    return oi / rev


def fcf_margin(fy: FY) -> float | None:
    rev = fy.income.get("revenue")
    if not rev:
        return None
    ocf = fy.cashflow.get("operatingCashFlow")
    capex = fy.cashflow.get("investmentsInPropertyPlantAndEquipment")  # no capitalExpenditure (redondeado 2020-21)
    if ocf is not None and capex is not None:
        fcf = ocf - abs(capex)
    else:
        fcf = fy.cashflow.get("freeCashFlow")
    return None if fcf is None else fcf / rev


# ----------------------------------------------------------------------------
# Cruce con yfinance
# ----------------------------------------------------------------------------
@dataclass
class Check:
    ok: bool                 # False = LSE no disponible para este ticker (nota en na_note)
    na_note: str = ""
    op_note: str = ""        # vacío = sin divergencia (aplica a filtros 1 y 3)
    fcf_note: str = ""       # vacío = sin divergencia (aplica al filtro 4)


def _match(annual: dict[date, FY], d: date) -> FY | None:
    best = min(annual, key=lambda e: abs((e - d).days), default=None)
    if best is not None and abs((best - d).days) <= MATCH_DAYS:
        return annual[best]
    return None


def _years(dates: list[date]) -> str:
    ys = sorted({d.year for d in dates})
    return f"{ys[0]}" if len(ys) == 1 else f"{ys[0]}-{ys[-1]}"


def compare(annual: dict[date, FY], yf_op: dict[date, float], yf_fcf: dict[date, float]) -> Check:
    """Compara márgenes sobre los años fiscales que ambas fuentes tienen en común."""
    cur_note = ""
    op_pairs: list[tuple[date, float, float]] = []
    for d, y in yf_op.items():
        fy = _match(annual, d)
        m = op_margin(fy) if fy else None
        if m is not None:
            op_pairs.append((d, m, y))
            if fy.currency and fy.currency != "USD":
                cur_note = f"; LSE reporta en {fy.currency}, se comparan márgenes (no importes)"
    fcf_pairs: list[tuple[date, float, float]] = []
    for d, y in yf_fcf.items():
        fy = _match(annual, d)
        m = fcf_margin(fy) if fy else None
        if m is not None:
            fcf_pairs.append((d, m, y))
            if fy.currency and fy.currency != "USD":
                cur_note = f"; LSE reporta en {fy.currency}, se comparan márgenes (no importes)"

    if not op_pairs and not fcf_pairs:
        return Check(False, "LSE N/A: sin años fiscales en común con yfinance")

    chk = Check(True)
    if op_pairs:
        lse_avg = statistics.mean(p[1] for p in op_pairs)
        yf_avg = statistics.mean(p[2] for p in op_pairs)
        gap = (lse_avg - yf_avg) * 100
        if abs(gap) > DIVERGENCE_PP:
            chk.op_note = (
                f"LSE difiere de yfinance en margen operativo medio: LSE {lse_avg*100:.1f}% vs yfinance "
                f"{yf_avg*100:.1f}% (Δ{abs(gap):.1f} pp, FY {_years([p[0] for p in op_pairs])}); "
                f"definiciones de utilidad operativa distintas, solo informativo{cur_note}"
            )
    if fcf_pairs:
        d, lse_m, yf_m = max(fcf_pairs, key=lambda p: p[0])  # último año común, como el filtro 4
        gap = (lse_m - yf_m) * 100
        if abs(gap) > DIVERGENCE_PP:
            chk.fcf_note = (
                f"LSE difiere de yfinance en margen FCF: LSE {lse_m*100:.1f}% vs yfinance "
                f"{yf_m*100:.1f}% (Δ{abs(gap):.1f} pp, FY {d.year}); solo informativo{cur_note}"
            )
    return chk


def check(ticker: str, yf_op: dict[date, float], yf_fcf: dict[date, float]) -> Check:
    """Punto de entrada. Nunca lanza: cualquier fallo de LSE devuelve Check(ok=False, nota)."""
    global _blocked
    key = api_key()
    if not key:
        return Check(False, f"{ENV_VAR} no definida")
    if _blocked:
        return Check(False, f"LSE N/A: {_blocked}")
    try:
        annual = annual_reports(ticker, key)
    except LseError as e:
        if e.status == 401:
            _blocked = str(e)
        return Check(False, f"LSE N/A: {e}")
    except Exception as e:  # noqa: BLE001
        return Check(False, f"LSE N/A: error al leer datos ({type(e).__name__})")
    return compare(annual, yf_op, yf_fcf)
