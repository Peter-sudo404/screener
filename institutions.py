"""
Módulo 2: posiciones de gestores institucionales grandes (13F-HR).

Cómo funciona:
  1. Lista fija de gestores (abajo) con su CIK en EDGAR. Separados en tres grupos:
       - activos: gestoras tradicionales de gestión activa (T. Rowe, Capital, Wellington, Dodge & Cox, ...)
       - hedge:   fondos de convicción (Berkshire, Pershing, Third Point, Baupost, ...)
       - pasivos: índices y bancos custodios (Vanguard, BlackRock, State Street, Geode, JPM, ...).
         Sus cambios reflejan flujos de índices, no una opinión. Se muestran pero NO cuentan
         para la señal de convergencia.
     Se excluyen a propósito los cuantitativos/multiestrategia (Renaissance, Citadel, Millennium,
     Two Sigma, Bridgewater): sus 13F mezclan coberturas, arbitraje y opciones y no significan nada
     sobre convicción en una empresa.
  2. Para cada gestor descarga la tabla de posiciones de sus 2 últimos 13F-HR (dos trimestres).
  3. Mapea cada ticker a su CUSIP con el archivo público de fails-to-deliver de la SEC
     (contiene CUSIP|SYMBOL para casi todo lo que cotiza en EE.UU.).
  4. Cruza y calcula: tiene posición, cambio en acciones vs. trimestre anterior (ajustado por splits),
     peso de la posición dentro del 13F del gestor.
  5. Convergencia sectorial: cuántos gestores activos/hedge tienen posición en >=2 empresas del
     mismo sector *dentro del universo analizado* (no de todo el mercado: eso requeriría
     clasificar sectorialmente miles de CUSIPs).

Rezago: un 13F se presenta hasta 45 días después del cierre del trimestre. La foto siempre
tiene entre 1.5 y 4.5 meses de antigüedad. El reporte lo dice explícitamente.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta

import edgar

# (nombre corto, CIK, grupo). CIKs verificados contra EDGAR el 2026-09-14.
MANAGERS: list[tuple[str, int, str]] = [
    # --- activos tradicionales
    ("T. Rowe Price", 80255, "activo"),
    ("Capital Research Global", 1422848, "activo"),
    ("Capital World Investors", 1422849, "activo"),
    ("Wellington", 902219, "activo"),
    ("Fidelity (FMR)", 315066, "activo"),
    ("Dodge & Cox", 200217, "activo"),
    ("Primecap", 763212, "activo"),
    ("Harris Associates", 813917, "activo"),
    ("Baillie Gifford", 1088875, "activo"),
    ("Davis Selected", 1036325, "activo"),
    ("Akre Capital", 1112520, "activo"),
    ("Fundsmith", 1569205, "activo"),
    ("AllianceBernstein", 1109448, "activo"),
    ("Franklin Templeton", 38777, "activo"),
    # --- hedge / convicción
    ("Berkshire Hathaway", 1067983, "hedge"),
    ("Pershing Square", 1336528, "hedge"),
    ("Third Point", 1040273, "hedge"),
    ("Appaloosa", 1656456, "hedge"),
    ("Tiger Global", 1167483, "hedge"),
    ("Coatue", 1135730, "hedge"),
    ("Lone Pine", 1061165, "hedge"),
    ("Viking Global", 1103804, "hedge"),
    ("Baupost", 1061768, "hedge"),
    ("Elliott", 1791786, "hedge"),
    ("Icahn", 921669, "hedge"),
    ("ValueAct", 1418814, "hedge"),
    ("Soros Fund Mgmt", 1029160, "hedge"),
    ("Duquesne (Druckenmiller)", 1536411, "hedge"),
    ("D1 Capital", 1747057, "hedge"),
    ("Altimeter", 1541617, "hedge"),
    # --- pasivos / custodios (solo informativo)
    ("Vanguard", 102909, "pasivo"),
    ("BlackRock", 2012383, "pasivo"),
    ("State Street", 93751, "pasivo"),
    ("Geode", 1214717, "pasivo"),
    ("Northern Trust", 73124, "pasivo"),
    ("JPMorgan", 19617, "pasivo"),
    ("Morgan Stanley", 895421, "pasivo"),
    ("Goldman Sachs", 886982, "pasivo"),
    ("Invesco", 914208, "pasivo"),
]

STALE_DAYS = 200  # si el último 13F es más viejo que esto, el gestor se marca como desactualizado


@dataclass
class Holding:
    shares: float
    value_usd: float


@dataclass
class ManagerData:
    name: str
    cik: int
    group: str
    latest_period: date | None = None
    prior_period: date | None = None
    latest: dict[str, Holding] = field(default_factory=dict)   # cusip -> holding
    prior: dict[str, Holding] = field(default_factory=dict)
    latest_total: float = 0.0
    error: str = ""
    stale: bool = False


@dataclass
class Position:
    manager: str
    group: str
    period: date
    shares: float
    value_usd: float
    weight_pct: float           # % del 13F del gestor
    prior_shares: float | None  # None = no había trimestre anterior disponible
    change: str                 # NUEVA / SALIO / +x% / -x% / = / n/d


@dataclass
class InstReport:
    ticker: str
    cusip: str | None
    positions: list[Position] = field(default_factory=list)
    note: str = ""

    def signal_positions(self) -> list[Position]:
        return [p for p in self.positions if p.group != "pasivo" and p.change != "SALIO"]

    def short(self) -> str:
        if self.cusip is None:
            return "N/A"
        sig = [p for p in self.positions if p.group != "pasivo"]
        held = [p for p in sig if p.change != "SALIO"]
        up = sum(1 for p in held if p.change == "NUEVA" or p.change.startswith("+"))
        down = sum(1 for p in held if p.change.startswith("-"))
        out = sum(1 for p in sig if p.change == "SALIO")
        n_active = sum(1 for m in _managers_loaded if m.group != "pasivo" and not m.error and not m.stale)
        return f"{len(held)}/{n_active} activos/hedge (+{up} -{down} salio {out})"


# ----------------------------------------------------------------------------
# CUSIP map (fails-to-deliver de la SEC)
# ----------------------------------------------------------------------------
_cusip_map: dict[str, tuple[str, str]] | None = None  # SYMBOL -> (cusip, description)


def _load_cusip_map() -> dict[str, tuple[str, str]]:
    global _cusip_map
    if _cusip_map is not None:
        return _cusip_map
    m: dict[str, tuple[str, str]] = {}
    d = date.today()
    tried = 0
    # buscar el archivo más reciente disponible (se publican con ~2-3 semanas de rezago)
    while tried < 6 and len(m) < 1000:
        for half in ("b", "a"):
            name = f"cnsfails{d.year}{d.month:02d}{half}"
            try:
                z = edgar.get(f"https://www.sec.gov/files/data/fails-deliver-data/{name}.zip", retries=1)
            except Exception:  # noqa: BLE001
                continue
            zf = zipfile.ZipFile(io.BytesIO(z))
            txt = zf.read(zf.namelist()[0]).decode("latin-1")
            for line in txt.splitlines()[1:]:
                parts = line.split("|")
                if len(parts) >= 5 and parts[1] and parts[2]:
                    m.setdefault(parts[2].strip().upper(), (parts[1].strip(), parts[4].strip()))
            if len(m) >= 1000:
                break
        d = (d.replace(day=1) - timedelta(days=1))
        tried += 1
    _cusip_map = m
    return m


def ticker_cusip(ticker: str) -> str | None:
    m = _load_cusip_map()
    tk = ticker.upper()
    for cand in (tk, tk.replace("-", ""), tk.replace(".", ""), tk.replace("-", ".")):
        if cand in m:
            return m[cand][0]
    return None


# ----------------------------------------------------------------------------
# 13F parsing
# ----------------------------------------------------------------------------
def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _parse_info_table(xml: bytes) -> tuple[dict[str, Holding], float]:
    holdings: dict[str, Holding] = {}
    total = 0.0
    for _, el in ET.iterparse(io.BytesIO(xml)):
        if _strip_ns(el.tag) != "infoTable":
            continue
        d = {_strip_ns(c.tag): c for c in el}
        cusip = (d["cusip"].text or "").strip().upper() if "cusip" in d else ""
        put_call = ""
        shares = 0.0
        if "shrsOrPrnAmt" in d:
            for c in d["shrsOrPrnAmt"]:
                t = _strip_ns(c.tag)
                if t == "sshPrnamt":
                    try:
                        shares = float(c.text or 0)
                    except ValueError:
                        shares = 0.0
                elif t == "sshPrnamtType" and (c.text or "").strip() != "SH":
                    shares = 0.0  # principal amount (bonos), no acciones
        if "putCall" in d:
            put_call = (d["putCall"].text or "").strip()
        try:
            value = float((d["value"].text or 0)) if "value" in d else 0.0
        except ValueError:
            value = 0.0
        el.clear()
        if not cusip or put_call:
            continue  # opciones no cuentan como posición
        total += value
        h = holdings.get(cusip)
        if h:
            h.shares += shares
            h.value_usd += value
        else:
            holdings[cusip] = Holding(shares, value)
    return holdings, total


_managers_loaded: list[ManagerData] = []


def _load_manager(name: str, cik: int, group: str) -> ManagerData:
    md = ManagerData(name=name, cik=cik, group=group)
    try:
        filings = edgar.recent_filings(cik, {"13F-HR"})
    except Exception as e:  # noqa: BLE001
        md.error = f"EDGAR: {str(e)[:60]}"
        return md
    # dos periodos distintos más recientes
    by_period: dict[str, dict] = {}
    for f in filings:
        by_period.setdefault(f["report_date"], f)  # el primero es el más reciente presentado
    periods = sorted(by_period, reverse=True)[:2]
    if not periods:
        md.error = "sin 13F-HR"
        return md
    md.latest_period = date.fromisoformat(periods[0])
    md.stale = (date.today() - md.latest_period).days > STALE_DAYS
    for i, per in enumerate(periods):
        f = by_period[per]
        try:
            items = edgar.filing_index(cik, f["accession"])
            xmls = [it["name"] for it in items if it["name"].lower().endswith(".xml") and "primary_doc" not in it["name"].lower()]
            if not xmls:
                raise RuntimeError("sin tabla de posiciones xml")
            xml = edgar.get(f"{edgar.filing_dir(cik, f['accession'])}/{xmls[0]}")
            holdings, total = _parse_info_table(xml)
            # Algunos gestores siguen reportando el valor en miles de USD (regla antigua).
            # Heurística: precio implícito mediano (valor/acciones). Si es < $1, está en miles.
            implied = sorted(h.value_usd / h.shares for h in holdings.values() if h.shares > 0 and h.value_usd > 0)
            if implied and implied[len(implied) // 2] < 1.0:
                for h in holdings.values():
                    h.value_usd *= 1000
                total *= 1000
        except Exception as e:  # noqa: BLE001
            if i == 0:
                md.error = f"tabla 13F: {str(e)[:60]}"
                return md
            continue
        if i == 0:
            md.latest, md.latest_total = holdings, total
        else:
            md.prior, md.prior_period = holdings, date.fromisoformat(per)
    return md


def load_managers(progress=None) -> list[ManagerData]:
    global _managers_loaded
    if _managers_loaded:
        return _managers_loaded
    out = []
    for name, cik, group in MANAGERS:
        if progress:
            progress(f"13F {name}")
        out.append(_load_manager(name, cik, group))
    _managers_loaded = out
    return out


# ----------------------------------------------------------------------------
# Cruce por ticker
# ----------------------------------------------------------------------------
def _split_factor(ticker: str, start: date, end: date) -> float:
    """Factor acumulado de splits entre dos fechas (para comparar acciones entre trimestres)."""
    try:
        import yfinance as yf

        sp = yf.Ticker(ticker).splits
        f = 1.0
        for ts, ratio in sp.items():
            d = ts.date()
            if start < d <= end and ratio:
                f *= float(ratio)
        return f
    except Exception:  # noqa: BLE001
        return 1.0


def analyze(ticker: str, managers: list[ManagerData] | None = None) -> InstReport:
    managers = managers or load_managers()
    cusip = ticker_cusip(ticker)
    rep = InstReport(ticker=ticker.upper(), cusip=cusip)
    if not cusip:
        rep.note = "no se encontró CUSIP para el ticker (no cotiza en EE.UU. o no está en el archivo de la SEC)"
        return rep
    split_cache: dict[tuple[date, date], float] = {}
    for m in managers:
        if m.error:
            continue
        h = m.latest.get(cusip)
        p = m.prior.get(cusip) if m.prior_period else None
        if not h and not p:
            continue
        if h:
            prior_shares = None
            change = "n/d"
            if m.prior_period:
                if p:
                    key = (m.prior_period, m.latest_period)
                    if key not in split_cache:
                        split_cache[key] = _split_factor(ticker, *key)
                    adj = p.shares * split_cache[key]
                    prior_shares = adj
                    delta = (h.shares - adj) / adj if adj else 0.0
                    change = "=" if abs(delta) < 0.01 else f"{delta:+.0%}"
                else:
                    change = "NUEVA"
            weight = (h.value_usd / m.latest_total * 100) if m.latest_total else 0.0
            rep.positions.append(
                Position(m.name, m.group, m.latest_period, h.shares, h.value_usd, weight, prior_shares, change)
            )
        elif p:
            rep.positions.append(
                Position(m.name, m.group, m.latest_period, 0.0, 0.0, 0.0, p.shares, "SALIO")
            )
    rep.positions.sort(key=lambda x: ({"hedge": 0, "activo": 1, "pasivo": 2}[x.group], -x.weight_pct))
    return rep


def convergence(reports: dict[str, InstReport], sectors: dict[str, str]) -> list[dict]:
    """Por sector del universo: gestores activos/hedge con posición en >=2 empresas del sector."""
    by_sector: dict[str, list[str]] = {}
    for tk, sec in sectors.items():
        if sec:
            by_sector.setdefault(sec, []).append(tk)
    rows = []
    for sec, tks in by_sector.items():
        if len(tks) < 2:
            continue
        holders: dict[str, list[str]] = {}
        increasers: dict[str, list[str]] = {}
        for tk in tks:
            rep = reports.get(tk)
            if not rep:
                continue
            for p in rep.positions:
                if p.group == "pasivo" or p.change == "SALIO":
                    continue
                holders.setdefault(p.manager, []).append(tk)
                if p.change == "NUEVA" or p.change.startswith("+"):
                    increasers.setdefault(p.manager, []).append(tk)
        multi = {m: t for m, t in holders.items() if len(t) >= 2}
        multi_up = {m: t for m, t in increasers.items() if len(t) >= 2}
        rows.append(
            {
                "sector": sec,
                "empresas": tks,
                "gestores_con_2_o_mas": multi,
                "gestores_aumentando_2_o_mas": multi_up,
            }
        )
    return rows


def lag_disclaimer(managers: list[ManagerData]) -> str:
    periods = sorted({m.latest_period for m in managers if m.latest_period and not m.error}, reverse=True)
    stale = [m.name for m in managers if m.stale or m.error]
    txt = (
        "Los 13F se presentan hasta 45 días después del cierre de trimestre. "
        f"Periodo más reciente usado: {periods[0] if periods else 'n/d'}. "
        "Esto NO es dato en tiempo real: la foto tiene entre 1.5 y 4.5 meses de antigüedad y solo muestra "
        "posiciones largas en acciones de EE.UU. (sin cortos, sin coberturas)."
    )
    if stale:
        txt += f" Gestores con 13F desactualizado o sin datos, excluidos de la señal: {', '.join(stale)}."
    return txt


def to_markdown(rep: InstReport) -> str:
    if rep.cusip is None:
        return f"**13F institucionales:** N/A — {rep.note}"
    lines = [f"**13F institucionales:** {rep.short()}"]
    if rep.positions:
        lines.append("")
        lines.append("| Gestor | Grupo | Periodo | Acciones | Valor USD | % de su 13F | vs. trim. anterior |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in rep.positions:
            lines.append(
                f"| {p.manager} | {p.group} | {p.period} | {p.shares:,.0f} | {p.value_usd/1e6:,.0f}M | "
                f"{p.weight_pct:.2f}% | {p.change} |"
            )
    else:
        lines.append("Ningún gestor de la lista reporta posición.")
    return "\n".join(lines)
