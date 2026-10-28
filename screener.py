"""
Screener de 5 filtros fundamentales.

Uso:
    python screener.py MSFT AAPL NVDA
    python screener.py --file tickers.txt --out resultados
    python screener.py MSFT --no-sec        # salta EDGAR (filtro 2 queda N/A)

Fuentes:
    - yfinance (Yahoo Finance): estados financieros anuales (hasta 4 años),
      múltiplos, crecimiento, descripción del negocio.
    - SEC EDGAR: último 10-K, para buscar concentración de clientes.
      Solo aplica a empresas que reportan a la SEC (US-listed).

El resultado es objetivo: PASS / WARN / FAIL / N/A por filtro, con nota.
No es una recomendación de compra.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf
from tabulate import tabulate

# ----------------------------------------------------------------------------
# Umbrales. Están aquí arriba para que sean fáciles de ajustar.
# ----------------------------------------------------------------------------
T = {
    # F1 Rentabilidad
    "op_margin_min": 0.05,          # margen operativo mínimo para PASS directo
    "gross_margin_path_min": 0.30,  # si no es rentable, margen bruto mínimo para "camino claro"
    # F2 Clientes
    "customer_warn_pct": 10.0,      # un cliente >= 10% de ingresos -> WARN
    "customer_fail_pct": 25.0,      # un cliente >= 25% de ingresos -> FAIL
    # F3 Moat
    "moat_avg_op_margin_min": 0.10,
    "moat_gross_margin_min": 0.30,
    "moat_margin_cv_max": 0.25,     # coef. de variación del margen operativo (desv/media)
    "moat_roe_min": 0.20,           # ROE mínimo para aceptar moat de bajo costo (márgenes bajos pero estables)
    # F4 FCF
    "fcf_min_years": 3,
    "fcf_margin_min": 0.05,         # FCF / ingresos (último año)
    # F5 Valuación
    "peg_pass": 2.0,
    "peg_warn": 3.0,
    "ev_ebitda_cheap": 12.0,        # si EV/EBITDA <= esto y crecimiento >= 0, pasa aunque no haya PEG
    "growth_cap": 0.40,             # crecimiento máximo que se acepta para calcular PEG (conservador)
}

MOAT_KEYWORDS = {
    "efectos de red": ["network effect", "network of", "two-sided", "marketplace", "ecosystem"],
    "costos de cambio": ["switching cost", "mission-critical", "embedded", "integrated into", "workflow"],
    "recurrencia": ["subscription", "recurring revenue", "annual recurring", "renewal"],
    "propiedad intelectual": ["patent", "proprietary", "intellectual property", "trade secret"],
    "marca": ["brand", "brands", "premium"],
    "escala/costo": ["scale", "largest", "leading provider", "market leader", "low-cost", "leading supplier", "leading"],
    "tecnología/barreras": ["sole", "only company", "most advanced", "complex", "barriers", "design win", "specialized"],
    "regulatorio/licencia": ["regulated", "license", "licensed", "fda approval", "approval"],
}

# Formato de resultado -------------------------------------------------------
PASS, WARN, FAIL, NA = "PASS", "WARN", "FAIL", "N/A"


@dataclass
class FilterResult:
    status: str
    note: str


@dataclass
class CompanyResult:
    ticker: str
    name: str = ""
    sector: str = ""
    filters: dict[str, FilterResult] = field(default_factory=dict)
    error: str = ""
    insiders: object = None     # insiders.InsiderReport
    inst: object = None         # institutions.InstReport
    bottlenecks: object = None  # bottlenecks.BottleneckReport


# ----------------------------------------------------------------------------
# Utilidades de datos
# ----------------------------------------------------------------------------
def _row(df: pd.DataFrame, *names: str) -> pd.Series | None:
    """Devuelve la primera fila que exista entre `names`, ordenada de más
    antigua a más reciente y sin NaN."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return s.sort_index()
    return None


def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x*100:.1f}%"


def _num(x: float | None) -> str:
    if x is None:
        return "n/d"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"{x/div:.1f}{unit}"
    return f"{x:.0f}"


def _trend(series: list[float]) -> str:
    """Describe la tendencia de una serie ordenada de antigua a reciente."""
    if len(series) < 2:
        return "sin historial"
    ups = sum(1 for a, b in zip(series, series[1:]) if b > a)
    downs = len(series) - 1 - ups
    if downs == 0:
        return "creciente"
    if ups == 0:
        return "decreciente"
    return "mixta"


# ----------------------------------------------------------------------------
# Filtro 1: Rentabilidad o camino claro a ella
# ----------------------------------------------------------------------------
def f1_profitability(fin: pd.DataFrame, info: dict) -> FilterResult:
    rev = _row(fin, "Total Revenue", "Operating Revenue")
    opi = _row(fin, "Operating Income", "Total Operating Income As Reported", "EBIT")
    gp = _row(fin, "Gross Profit")
    if rev is None or opi is None:
        return FilterResult(NA, "sin ingresos/utilidad operativa en yfinance")

    idx = rev.index.intersection(opi.index)
    if len(idx) == 0:
        return FilterResult(NA, "años no coinciden")
    rev, opi = rev.loc[idx], opi.loc[idx]
    margins = (opi / rev).tolist()
    m_last = margins[-1]
    trend = _trend(opi.tolist())
    gm = (gp.loc[idx[-1]] / rev.iloc[-1]) if gp is not None and idx[-1] in gp.index else None

    if m_last >= T["op_margin_min"]:
        status = PASS if trend != "decreciente" else WARN
        return FilterResult(
            status,
            f"margen op. {_pct(m_last)} (últimos {len(margins)}a: {', '.join(_pct(m) for m in margins)}); "
            f"utilidad op. {trend}",
        )
    if m_last > 0:
        return FilterResult(WARN, f"margen op. positivo pero bajo: {_pct(m_last)}; utilidad op. {trend}")

    # No rentable: ¿hay camino claro? Pérdidas reduciéndose + margen bruto sano.
    improving = trend == "creciente" and len(margins) >= 3
    if improving and gm is not None and gm >= T["gross_margin_path_min"]:
        return FilterResult(
            WARN,
            f"no rentable ({_pct(m_last)}) pero pérdida operativa se reduce cada año y margen bruto {_pct(gm)}",
        )
    return FilterResult(FAIL, f"margen op. {_pct(m_last)}, utilidad op. {trend}, margen bruto {_pct(gm)}")


# ----------------------------------------------------------------------------
# Filtro 2: Diversificación de clientes (10-K vía EDGAR)
# ----------------------------------------------------------------------------
_cik_cache: dict[str, str] | None = None


def _sec_get(url: str, retries: int = 3) -> bytes:
    """Descarga vía el cliente compartido (throttle + caché en disco)."""
    import edgar

    ttl = 1 if "submissions" in url else (7 if "company_tickers" in url else None)
    return edgar.get(url, retries=retries, ttl_days=ttl)


def _ticker_to_cik(ticker: str) -> str | None:
    global _cik_cache
    if _cik_cache is None:
        data = json.loads(_sec_get("https://www.sec.gov/files/company_tickers.json"))
        _cik_cache = {v["ticker"].upper(): f"{int(v['cik_str']):010d}" for v in data.values()}
    tk = ticker.upper()
    for cand in (tk, tk.replace("-", "."), tk.replace(".", "-"), tk.replace("-", "")):
        if cand in _cik_cache:
            return _cik_cache[cand]
    return None


def _latest_10k_text(cik: str) -> tuple[str, str] | None:
    sub = json.loads(_sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    recent = sub["filings"]["recent"]
    for form, acc, doc, date in zip(
        recent["form"], recent["accessionNumber"], recent["primaryDocument"], recent["filingDate"]
    ):
        if form in ("10-K", "20-F", "40-F"):
            acc_nodash = acc.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
            raw = _sec_get(url).decode("utf-8", errors="ignore")
            text = re.sub(r"<[^>]+>", " ", raw)
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text)
            return text, f"{form} {date}"
    return None


_CUST_RE = re.compile(
    r"((?:no\s+(?:single\s+|one\s+|individual\s+)?)?"
    r"(?:customer|customers|distributor|distributors|client|clients|reseller|resellers|retailer|retailers)"
    r"[^.;]{0,120}?"
    r"(?:accounted\s+for|represented|represents|comprised|constituted|made\s+up|contributed|generated|was|were)"
    r"[^.;]{0,60}?"
    r"(\d{1,2}(?:\.\d)?)\s?%"
    r"[^.;]{0,80}?"
    r"(?:of\s+(?:our|the\s+company's|total|net|consolidated)?\s*(?:total\s+)?"
    r"(?:revenue|revenues|net\s+sales|sales|net\s+revenue|accounts\s+receivable|receivables)))",
    re.IGNORECASE,
)
# Negación: "No customer...", "No sales to an individual customer...", "none of our customers..."
_NEG_RE = re.compile(r"\b(?:no|none|neither|not\s+any)\b[^.;]{0,50}$", re.IGNORECASE)


def f2_customers(ticker: str, enabled: bool) -> FilterResult:
    if not enabled:
        return FilterResult(NA, "EDGAR desactivado (--no-sec)")
    try:
        cik = _ticker_to_cik(ticker)
        if not cik:
            return FilterResult(NA, "no reporta a la SEC (sin CIK); revisar informe anual manualmente")
        res = _latest_10k_text(cik)
        if not res:
            return FilterResult(NA, "sin 10-K/20-F reciente en EDGAR")
        text, src = res
    except Exception as e:  # noqa: BLE001
        return FilterResult(NA, f"error EDGAR: {str(e)[:80]}")

    hits: list[tuple[float, str]] = []
    negations: list[str] = []
    for m in _CUST_RE.finditer(text):
        sentence = m.group(1)
        if re.search(r"satisfaction|survey|employees|headcount|headquartered|located|outside\s+(?:of\s+)?the|geograph|region|country|countries|international|foreign", sentence, re.I):
            continue  # frases geográficas o de otro tema, no concentración de clientes
        pre = text[max(0, m.start() - 60):m.start()]
        if _NEG_RE.search(pre) or re.search(r"\b(?:no|none)\s+(?:single\s+|one\s+|individual\s+)?(?:customer|client)", sentence, re.I):
            negations.append((pre[-50:] + sentence).strip())
            continue
        pct = float(m.group(2))
        if 5 <= pct <= 100:
            # incluir contexto previo ("three largest ...") para poder distinguir grupo vs. un cliente
            hits.append((pct, (pre[-40:] + sentence).strip()))

    if not hits and negations:
        snip = negations[0][:150]
        return FilterResult(PASS, f"{src}: declara explícitamente sin cliente concentrado. «{snip}…»")
    if not hits:
        return FilterResult(
            PASS,
            f"{src}: no se detectó ningún cliente >=10% de ingresos (la SEC exige revelarlo si existe)",
        )
    # Prioridad: concentración de ingresos. Cuentas por cobrar solo si no hay otra cosa.
    rev_hits = [h for h in hits if not re.search(r"receivable", h[1], re.I)]
    pct, sent = max(rev_hits or hits, key=lambda h: h[0])
    snippet = (sent[:170] + "…") if len(sent) > 170 else sent
    # ¿El porcentaje es de un grupo ("three customers accounted for 43%") o de uno solo?
    _NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10}
    grp = re.search(r"\b(two|three|four|five|six|ten|\d)\s+(?:[A-Za-z]+\s+){0,3}?(?:customers|distributors|clients)\b", sent, re.I)
    n_group = (_NUM.get(grp.group(1).lower()) or int(grp.group(1))) if grp else 1
    if n_group > 1:
        per_client = pct / n_group
        label = f"grupo de {n_group} clientes/canales ~{pct:.0f}% (≈{per_client:.0f}% c/u)"
        status = FAIL if per_client >= T["customer_fail_pct"] else (WARN if pct >= T["customer_fail_pct"] or per_client >= T["customer_warn_pct"] else PASS)
        return FilterResult(status, f"{src}: {label}. «{snippet}»")
    if pct >= T["customer_fail_pct"]:
        return FilterResult(FAIL, f"{src}: un cliente/canal ~{pct:.0f}% de ingresos. «{snippet}»")
    if pct >= T["customer_warn_pct"]:
        return FilterResult(WARN, f"{src}: un cliente/canal ~{pct:.0f}% de ingresos. «{snippet}»")
    return FilterResult(PASS, f"{src}: mayor concentración detectada ~{pct:.0f}%. «{snippet}»")


# ----------------------------------------------------------------------------
# Filtro 3: Ventaja competitiva durable
# ----------------------------------------------------------------------------
def f3_moat(fin: pd.DataFrame, info: dict) -> FilterResult:
    rev = _row(fin, "Total Revenue", "Operating Revenue")
    opi = _row(fin, "Operating Income", "Total Operating Income As Reported", "EBIT")
    gp = _row(fin, "Gross Profit")
    desc = (info.get("longBusinessSummary") or "").lower()

    found = [k for k, words in MOAT_KEYWORDS.items() if any(w in desc for w in words)]
    qual = f"señales en descripción: {', '.join(found)}" if found else "sin señales de moat en descripción"

    if rev is None or opi is None:
        return FilterResult(NA, f"sin datos de márgenes; {qual}")
    idx = rev.index.intersection(opi.index)
    margins = (opi.loc[idx] / rev.loc[idx]).tolist()
    if len(margins) < 3:
        return FilterResult(NA, f"solo {len(margins)} años de datos; {qual}")

    avg = statistics.mean(margins)
    cv = (statistics.pstdev(margins) / abs(avg)) if avg != 0 else float("inf")
    gm = (gp.loc[idx[-1]] / rev.loc[idx[-1]]) if gp is not None and idx[-1] in gp.index else None

    roe = info.get("returnOnEquity") if isinstance(info.get("returnOnEquity"), (int, float)) else None

    quant_ok = avg >= T["moat_avg_op_margin_min"] and cv <= T["moat_margin_cv_max"]
    gm_ok = gm is None or gm >= T["moat_gross_margin_min"]
    # Moat de bajo costo (retail, distribución): márgenes bajos pero muy estables y ROE alto.
    lowcost_ok = cv <= T["moat_margin_cv_max"] and avg > 0 and roe is not None and roe >= T["moat_roe_min"]
    detail = (
        f"margen op. medio {_pct(avg)}, variabilidad {cv:.2f}, margen bruto {_pct(gm)}, "
        f"ROE {_pct(roe)}; {qual}"
    )

    if quant_ok and gm_ok and found:
        return FilterResult(PASS, detail)
    if quant_ok and gm_ok:
        return FilterResult(WARN, "márgenes estables pero moat no evidente cualitativamente; " + detail)
    if lowcost_ok:
        return FilterResult(WARN, "márgenes bajos pero estables con ROE alto (posible moat de costo/escala); " + detail)
    if avg >= T["moat_avg_op_margin_min"]:
        return FilterResult(WARN, "márgenes altos pero inestables (posible ciclicidad); " + detail)
    return FilterResult(FAIL, detail)


# ----------------------------------------------------------------------------
# Filtro 4: Flujo de caja libre
# ----------------------------------------------------------------------------
def f4_fcf(cf: pd.DataFrame, fin: pd.DataFrame) -> FilterResult:
    fcf = _row(cf, "Free Cash Flow")
    if fcf is None:
        ocf = _row(cf, "Operating Cash Flow")
        capex = _row(cf, "Capital Expenditure")
        if ocf is None:
            return FilterResult(NA, "sin flujo de caja en yfinance")
        fcf = ocf + (capex.reindex(ocf.index).fillna(0) if capex is not None else 0)
    rev = _row(fin, "Total Revenue", "Operating Revenue")
    ni = _row(fin, "Net Income")

    vals = fcf.tolist()
    years = len(vals)
    pos_years = sum(1 for v in vals if v > 0)
    fcf_margin = (vals[-1] / rev.loc[fcf.index[-1]]) if rev is not None and fcf.index[-1] in rev.index else None
    conv = (vals[-1] / ni.loc[fcf.index[-1]]) if ni is not None and fcf.index[-1] in ni.index and ni.loc[fcf.index[-1]] > 0 else None

    detail = (
        f"FCF {years}a: {', '.join(_num(v) for v in vals)}; margen FCF {_pct(fcf_margin)}"
        + (f"; FCF/utilidad neta {conv:.2f}" if conv is not None else "")
    )
    if years < T["fcf_min_years"]:
        return FilterResult(NA, f"solo {years} años de datos; " + detail)
    if pos_years == years and (fcf_margin is None or fcf_margin >= T["fcf_margin_min"]):
        return FilterResult(PASS, detail)
    if pos_years == years:
        return FilterResult(WARN, "FCF positivo todos los años pero margen FCF bajo; " + detail)
    if vals[-1] > 0 and pos_years >= years - 1:
        return FilterResult(WARN, "FCF positivo hoy, con un año negativo; " + detail)
    return FilterResult(FAIL, detail)


# ----------------------------------------------------------------------------
# Filtro 5: Valuación razonable relativa al crecimiento
# ----------------------------------------------------------------------------
def f5_valuation(fin: pd.DataFrame, info: dict) -> FilterResult:
    tpe = info.get("trailingPE")
    fpe = info.get("forwardPE")
    ev_ebitda = info.get("enterpriseToEbitda")
    if not isinstance(ev_ebitda, (int, float)) or ev_ebitda > 200:
        ev_ebitda = None  # yfinance a veces mezcla monedas (ADRs) y devuelve basura
    eps = _row(fin, "Diluted EPS", "Basic EPS")

    growths: dict[str, float] = {}
    # crecimiento implícito en consenso (PE trailing -> forward)
    if tpe and fpe and tpe > 0 and fpe > 0:
        growths["implícito consenso"] = tpe / fpe - 1
    # CAGR histórico de EPS
    if eps is not None and len(eps) >= 3 and eps.iloc[0] > 0 and eps.iloc[-1] > 0:
        n = len(eps) - 1
        growths["CAGR EPS hist."] = (eps.iloc[-1] / eps.iloc[0]) ** (1 / n) - 1
    # crecimiento de utilidad reportado por Yahoo (último trimestre yoy)
    eg = info.get("earningsGrowth")
    if isinstance(eg, (int, float)):
        growths["yoy último trim."] = eg

    if not growths:
        g = None
    else:
        g = min(statistics.median(growths.values()), T["growth_cap"])
    g_txt = ", ".join(f"{k} {_pct(v)}" for k, v in growths.items()) or "sin datos de crecimiento"
    if g is not None and g == T["growth_cap"]:
        g_txt += f" (capado a {_pct(g)} para el PEG)"

    pe_used = fpe if fpe and fpe > 0 else tpe
    mult_txt = f"P/E fwd {fpe:.1f}" if fpe else (f"P/E {tpe:.1f}" if tpe else "P/E n/d")
    mult_txt += f", EV/EBITDA {ev_ebitda:.1f}" if ev_ebitda else ", EV/EBITDA n/d"

    if not pe_used or pe_used <= 0:
        if ev_ebitda and ev_ebitda > 0 and ev_ebitda <= T["ev_ebitda_cheap"]:
            return FilterResult(WARN, f"sin utilidades (P/E negativo) pero {mult_txt}; {g_txt}")
        return FilterResult(FAIL, f"sin utilidades para valuar (P/E negativo o n/d); {mult_txt}")

    if g is None or g <= 0:
        if ev_ebitda and 0 < ev_ebitda <= T["ev_ebitda_cheap"]:
            return FilterResult(WARN, f"crecimiento nulo/negativo pero múltiplo bajo: {mult_txt}; {g_txt}")
        return FilterResult(FAIL, f"paga múltiplo sin crecimiento: {mult_txt}; {g_txt}")

    peg = pe_used / (g * 100)
    detail = f"PEG {peg:.2f} ({mult_txt}; crecimiento usado {_pct(g)}: {g_txt})"
    if peg <= T["peg_pass"]:
        return FilterResult(PASS, detail)
    if peg <= T["peg_warn"] or (ev_ebitda and 0 < ev_ebitda <= T["ev_ebitda_cheap"]):
        return FilterResult(WARN, detail)
    return FilterResult(FAIL, detail)


# ----------------------------------------------------------------------------
# Orquestación
# ----------------------------------------------------------------------------
FILTERS = ["1 Rentabilidad", "2 Clientes", "3 Moat", "4 FCF", "5 Valuación"]


def screen(ticker: str, use_sec: bool) -> CompanyResult:
    r = CompanyResult(ticker=ticker.upper())
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        fin = t.financials
        cf = t.cashflow
        if not info.get("longName") and (fin is None or fin.empty):
            r.error = "ticker no encontrado en Yahoo Finance"
            return r
        r.name = info.get("longName") or info.get("shortName") or ticker
        r.sector = info.get("sector") or ""
        r.filters["1 Rentabilidad"] = f1_profitability(fin, info)
        r.filters["2 Clientes"] = f2_customers(ticker, use_sec)
        r.filters["3 Moat"] = f3_moat(fin, info)
        r.filters["4 FCF"] = f4_fcf(cf, fin)
        r.filters["5 Valuación"] = f5_valuation(fin, info)
    except Exception as e:  # noqa: BLE001
        r.error = f"{type(e).__name__}: {str(e)[:120]}"
    return r


def to_frames(results: list[CompanyResult]) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows, detail_rows = [], []
    for r in results:
        if r.error:
            summary_rows.append({"Ticker": r.ticker, "Empresa": r.error, **{f: "ERR" for f in FILTERS}, "Pasa": 0})
            continue
        row = {"Ticker": r.ticker, "Empresa": r.name[:28]}
        for f in FILTERS:
            row[f] = r.filters[f].status
        row["Pasa"] = sum(1 for f in FILTERS if r.filters[f].status == PASS)
        if r.insiders is not None:
            row["Insiders 6m"] = r.insiders.short()
        if r.inst is not None:
            row["13F activos/hedge"] = r.inst.short()
        summary_rows.append(row)
        for f in FILTERS:
            detail_rows.append({"Ticker": r.ticker, "Filtro": f, "Resultado": r.filters[f].status, "Nota": r.filters[f].note})
        if r.insiders is not None:
            detail_rows.append({"Ticker": r.ticker, "Filtro": "Insiders (Form 4)", "Resultado": r.insiders.status, "Nota": r.insiders.note})
            for p in sorted(r.insiders.purchases, key=lambda p: p.date):
                detail_rows.append(
                    {
                        "Ticker": r.ticker, "Filtro": "Insider compra P", "Resultado": p.role,
                        "Nota": f"{p.date} {p.insider} ({p.title}) {p.shares:,.0f} acc @ {p.price or 'n/d'} = ${(p.usd or 0):,.0f}"
                        + (" indirecta" if p.indirect else "") + (" 10b5-1" if p.plan_10b5_1 else ""),
                    }
                )
        if r.inst is not None:
            detail_rows.append({"Ticker": r.ticker, "Filtro": "13F institucionales", "Resultado": r.inst.short(), "Nota": r.inst.note})
            for p in r.inst.positions:
                detail_rows.append(
                    {
                        "Ticker": r.ticker, "Filtro": f"13F {p.group}", "Resultado": p.change,
                        "Nota": f"{p.manager} | {p.period} | {p.shares:,.0f} acc | ${p.value_usd/1e6:,.0f}M | {p.weight_pct:.2f}% de su 13F",
                    }
                )
        if r.bottlenecks is not None:
            for s in r.bottlenecks.sentences:
                detail_rows.append({"Ticker": r.ticker, "Filtro": "Cuello de botella (texto)", "Resultado": "", "Nota": s})
    return pd.DataFrame(summary_rows), pd.DataFrame(detail_rows)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Screener de 5 filtros fundamentales (yfinance + SEC EDGAR).")
    ap.add_argument("tickers", nargs="*", help="tickers, ej. MSFT AAPL NVDA")
    ap.add_argument("--file", help="archivo con un ticker por línea")
    ap.add_argument("--out", default="resultados", help="prefijo de salida (.csv y .md)")
    ap.add_argument("--no-sec", action="store_true", help="no consultar EDGAR (filtro 2 = N/A)")
    ap.add_argument("--no-insiders", action="store_true", help="omitir módulo Form 4 (compras de insiders)")
    ap.add_argument("--no-13f", action="store_true", help="omitir módulo 13F (institucionales)")
    ap.add_argument("--no-bottlenecks", action="store_true", help="omitir sección de cuellos de botella")
    args = ap.parse_args()

    tickers = list(args.tickers)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            tickers += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not tickers:
        ap.error("indica al menos un ticker o --file")

    results = []
    for tk in tickers:
        _log(f"... {tk} (fundamentales)")
        results.append(screen(tk, use_sec=not args.no_sec))

    # ---- Módulo 1: insiders
    if not args.no_insiders:
        import insiders

        for r in results:
            if r.error:
                continue
            _log(f"... {r.ticker} (Form 4)")
            try:
                r.insiders = insiders.analyze(r.ticker)
            except Exception as e:  # noqa: BLE001
                r.insiders = insiders.InsiderReport(ticker=r.ticker, status="N/A", note=f"error: {str(e)[:80]}")

    # ---- Módulo 2: 13F
    convergence_rows: list[dict] = []
    lag_text = ""
    if not args.no_13f:
        import institutions

        _log("... cargando 13F de gestores (primera vez tarda ~4 min, luego queda en caché)")
        managers = institutions.load_managers(progress=lambda s: _log(f"    {s}"))
        lag_text = institutions.lag_disclaimer(managers)
        inst_reports = {}
        for r in results:
            if r.error:
                continue
            _log(f"... {r.ticker} (13F)")
            try:
                r.inst = institutions.analyze(r.ticker, managers)
            except Exception as e:  # noqa: BLE001
                r.inst = institutions.InstReport(ticker=r.ticker, cusip=None, note=f"error: {str(e)[:80]}")
            inst_reports[r.ticker] = r.inst
        convergence_rows = institutions.convergence(inst_reports, {r.ticker: r.sector for r in results if not r.error})

    # ---- Texto libre: cuellos de botella
    if not args.no_bottlenecks:
        import bottlenecks

        for r in results:
            if r.error:
                continue
            _log(f"... {r.ticker} (cuellos de botella 8-K/10-Q)")
            try:
                r.bottlenecks = bottlenecks.analyze(r.ticker)
            except Exception as e:  # noqa: BLE001
                r.bottlenecks = bottlenecks.BottleneckReport(ticker=r.ticker, note=f"error: {str(e)[:80]}")

    summary, detail = to_frames(results)

    # ---- Consola
    print("\n=== RESUMEN (PASS / WARN / FAIL / N/A) ===")
    print(tabulate(summary, headers="keys", tablefmt="github", showindex=False))
    if lag_text:
        print(f"\nAVISO 13F: {lag_text}")
    print("\n=== DETALLE ===")
    for r in results:
        print(f"\n{r.ticker} — {r.name}" + (f" [{r.sector}]" if r.sector else ""))
        if r.error:
            print(f"  ERROR: {r.error}")
            continue
        for f in FILTERS:
            fr = r.filters[f]
            print(f"  [{fr.status:4}] {f}: {fr.note}")
        if r.insiders is not None:
            print(f"  [INS ] Insiders 6m: {r.insiders.status} — {r.insiders.note}")
            for p in sorted(r.insiders.purchases, key=lambda p: p.date, reverse=True)[:8]:
                print(f"         {p.date} {p.role:9} {p.insider[:28]:28} ${(p.usd or 0):>12,.0f}" + ("  (indirecta)" if p.indirect else "") + ("  (10b5-1)" if p.plan_10b5_1 else ""))
        if r.inst is not None:
            print(f"  [13F ] {r.inst.short()} {r.inst.note}")
            for p in [x for x in r.inst.positions if x.group != 'pasivo'][:8]:
                print(f"         {p.manager[:24]:24} {p.group:7} {p.weight_pct:5.2f}% de su 13F  {p.change}")
        if r.bottlenecks is not None and r.bottlenecks.sentences:
            print(f"  [TXT ] Cuellos de botella ({'; '.join(r.bottlenecks.sources)}):")
            for s in r.bottlenecks.sentences[:5]:
                print(f"         - {s[:200]}")
    if convergence_rows:
        print("\n=== CONVERGENCIA SECTORIAL 13F (gestores activos/hedge con posición en >=2 empresas del mismo sector del universo) ===")
        for row in convergence_rows:
            print(f"  {row['sector']} ({', '.join(row['empresas'])}): {len(row['gestores_con_2_o_mas'])} gestores en >=2; "
                  f"{len(row['gestores_aumentando_2_o_mas'])} aumentando en >=2")
            for m, tks in list(row["gestores_aumentando_2_o_mas"].items())[:10]:
                print(f"      + {m}: {', '.join(tks)}")

    # ---- Archivos
    summary.to_csv(f"{args.out}_resumen.csv", index=False, encoding="utf-8-sig")
    detail.to_csv(f"{args.out}_detalle.csv", index=False, encoding="utf-8-sig")
    with open(f"{args.out}.md", "w", encoding="utf-8") as fh:
        fh.write("# Screener — resumen\n\n")
        fh.write(tabulate(summary, headers="keys", tablefmt="github", showindex=False))
        fh.write("\n\n")
        if lag_text:
            fh.write(f"> **Aviso 13F:** {lag_text}\n\n")
        if not args.no_insiders:
            fh.write("> **Insiders:** solo compras en mercado abierto con dinero propio (Form 4 código P). "
                     "FUERTE = CEO/CFO o >=3 insiders en la misma ventana de 30 días; MODERADA = varios insiders o compra grande de CEO/CFO; "
                     "DEBIL = compra aislada de un director; 10%-OWNER = solo accionistas mayoritarios.\n\n")
        fh.write("# Detalle\n\n")
        for r in results:
            fh.write(f"## {r.ticker} — {r.name}" + (f" [{r.sector}]" if r.sector else "") + "\n\n")
            if r.error:
                fh.write(f"ERROR: {r.error}\n\n")
                continue
            for f in FILTERS:
                fr = r.filters[f]
                fh.write(f"- **{f}** `{fr.status}` — {fr.note}\n")
            fh.write("\n")
            if r.insiders is not None:
                import insiders as _ins
                fh.write(_ins.to_markdown(r.insiders) + "\n\n")
            if r.inst is not None:
                import institutions as _inst
                fh.write(_inst.to_markdown(r.inst) + "\n\n")
            if r.bottlenecks is not None:
                import bottlenecks as _bn
                fh.write(_bn.to_markdown(r.bottlenecks) + "\n\n")
        if convergence_rows:
            fh.write("# Convergencia sectorial 13F\n\n")
            fh.write("Gestores activos/hedge (no pasivos) con posición en dos o más empresas del mismo sector "
                     "**dentro del universo analizado**. No es una lectura de todo el mercado.\n\n")
            for row in convergence_rows:
                fh.write(f"## {row['sector']} — {', '.join(row['empresas'])}\n\n")
                fh.write(f"- Gestores con posición en >=2 empresas: **{len(row['gestores_con_2_o_mas'])}**\n")
                for m, tks in row["gestores_con_2_o_mas"].items():
                    fh.write(f"  - {m}: {', '.join(tks)}\n")
                fh.write(f"- Gestores que aumentaron o abrieron en >=2 empresas el último trimestre: **{len(row['gestores_aumentando_2_o_mas'])}**\n")
                for m, tks in row["gestores_aumentando_2_o_mas"].items():
                    fh.write(f"  - {m}: {', '.join(tks)}\n")
                fh.write("\n")
    print(f"\nGuardado: {args.out}_resumen.csv, {args.out}_detalle.csv, {args.out}.md")


if __name__ == "__main__":
    main()
