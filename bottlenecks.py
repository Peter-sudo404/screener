"""
Sección de texto libre: cuellos de botella mencionados por la empresa.

Fuente gratuita y estructurada: EDGAR. No hay transcripciones de earnings calls en
EDGAR y las fuentes gratuitas de transcripciones (Motley Fool, etc.) bloquean scrapers y
cambian de formato. En su lugar se usan los dos documentos oficiales más cercanos a la
call:
  - el último 8-K de resultados (Item 2.02) con su Exhibit 99.1 (el press release que la
    gerencia comenta en la call), y
  - el último 10-Q / 10-K (sección MD&A y factores de riesgo).

Se extraen las frases que contienen palabras clave de restricción de oferta, capacidad,
suministro, mano de obra, regulación o demanda. Es texto sin puntuar, para leer, no un filtro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import edgar

KEYWORDS = [
    r"supply[- ]constrain", r"capacity[- ]constrain", r"supply chain constrain", r"constraints? on (?:power|cooling|supply|capacity|components?)",
    r"shortage", r"bottleneck", r"backlog", r"lead[- ]times?", r"unable to meet", r"could not meet demand",
    r"demand (?:exceeded|exceeds|outstrip)", r"exceeded (?:our )?(?:available )?(?:supply|capacity)",
    r"tariffs? (?:paid|imposed|impact|cost)", r"export (?:controls?|restrictions?|licens)", r"labor shortage",
    r"component availability", r"(?:memory|storage|gpu|cpu|wafer|substrate|helium|power|energy) (?:availability|shortage|supply)",
    r"construction delays?", r"production (?:delays?|ramp)",
]
_KW_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)
# Hipotéticos de "factores de riesgo" y boilerplate: no son cuellos de botella reales.
_NOISE_RE = re.compile(
    r"forward-looking|safe harbor|no assurance|\bcould\b|\bmight\b|\bmay\b|risk factor|we cannot guarantee|"
    r"from time to time|there can be no|if we (?:fail|are unable)|table of contents",
    re.IGNORECASE,
)


def _looks_like_toc(s: str) -> bool:
    words = s.split()
    caps = sum(1 for w in words if len(w) > 2 and w.isupper())
    return len(words) > 8 and caps / len(words) > 0.25
MAX_SENTENCES = 8


@dataclass
class BottleneckReport:
    ticker: str
    sources: list[str] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)
    note: str = ""


def _sentences(text: str) -> list[str]:
    # separar por punto seguido de mayúscula; limitar longitud
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if 60 <= len(p.strip()) <= 400]


def _extract(text: str, limit: int, seen: set | None = None) -> list[str]:
    out = []
    seen = seen if seen is not None else set()
    for s in _sentences(text):
        if not _KW_RE.search(s) or _NOISE_RE.search(s) or _looks_like_toc(s):
            continue
        key = re.sub(r"[^a-z]", "", s.lower())[:60]  # misma frase en 8-K y 10-K con comillas distintas
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _latest_earnings_8k(cik: int) -> tuple[str, str] | None:
    """Texto del Exhibit 99.1 del último 8-K que parece de resultados."""
    for f in edgar.recent_filings(cik, {"8-K"})[:10]:
        try:
            items = edgar.filing_index(cik, f["accession"])
        except Exception:  # noqa: BLE001
            continue
        ex = [
            it["name"] for it in items
            if re.search(r"ex[-_]?99|exhibit99|ex991", it["name"], re.I) and it["name"].lower().endswith((".htm", ".html"))
        ]
        if not ex:
            continue
        try:
            text = edgar.html_to_text(edgar.get(f"{edgar.filing_dir(cik, f['accession'])}/{ex[0]}"))
        except Exception:  # noqa: BLE001
            continue
        if re.search(r"(quarter|fiscal year).{0,200}(results|revenue|net sales)", text, re.I) and len(text) > 3000:
            return text, f"8-K {f['filed']} (press release de resultados)"
    return None


def _latest_periodic(cik: int) -> tuple[str, str] | None:
    fl = edgar.recent_filings(cik, {"10-Q", "10-K", "20-F"})
    if not fl:
        return None
    f = fl[0]
    text = edgar.html_to_text(edgar.get(f"{edgar.filing_dir(cik, f['accession'])}/{f['primary_doc']}"))
    return text, f"{f['form']} {f['filed']}"


def analyze(ticker: str) -> BottleneckReport:
    rep = BottleneckReport(ticker=ticker.upper())
    cik = edgar.ticker_to_cik(ticker)
    if not cik:
        rep.note = "sin CIK en la SEC"
        return rep
    cik_i = int(cik)
    seen: set = set()
    try:
        r8 = _latest_earnings_8k(cik_i)
        if r8:
            rep.sources.append(r8[1])
            rep.sentences += _extract(r8[0], MAX_SENTENCES, seen)
    except Exception as e:  # noqa: BLE001
        rep.note += f"8-K: {str(e)[:60]}. "
    try:
        rp = _latest_periodic(cik_i)
        if rp:
            rep.sources.append(rp[1])
            # en el 10-Q/10-K solo MD&A aproximado: saltar la primera parte (portada, riesgos genéricos)
            text = rp[0]
            # la primera mención suele ser el índice; la segunda es el encabezado real de la sección
            mdas = [m.start() for m in re.finditer(r"management.s discussion and analysis", text, re.I)]
            if len(mdas) >= 2:
                text = text[mdas[1]:]
            elif mdas and mdas[0] > 5000:
                text = text[mdas[0]:]
            rep.sentences += _extract(text, MAX_SENTENCES, seen)
    except Exception as e:  # noqa: BLE001
        rep.note += f"10-Q/K: {str(e)[:60]}."
    if not rep.sentences and not rep.note:
        rep.note = "sin menciones de restricciones de oferta/capacidad en los documentos revisados"
    return rep


def to_markdown(rep: BottleneckReport) -> str:
    lines = ["**Cuellos de botella mencionados por la empresa** (texto libre, no es filtro)"]
    if rep.sources:
        lines.append(f"Fuentes: {'; '.join(rep.sources)}")
    if rep.note:
        lines.append(rep.note)
    for s in rep.sentences:
        lines.append(f"- {s}")
    return "\n".join(lines)
