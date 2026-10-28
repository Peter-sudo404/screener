"""
Módulo 1: compras de insiders con dinero propio (Form 4, código "P").

Para cada emisor descarga los Form 4 de los últimos N meses desde EDGAR, parsea el
XML y se queda solo con transacciones no derivadas con código P (open-market
purchase) y A (adquisición). Excluye: M (ejercicio de opciones), A (otorgamiento),
F (retención fiscal), S (venta), G (regalo), C (conversión), etc.

Salida por empresa: lista de compras, y una etiqueta de patrón:
    FUERTE    : CEO o CFO comprando, o >=3 insiders distintos en una ventana de 30 días
    MODERADA  : >=2 insiders en 30 días, o una sola compra de un ejecutivo C-level >= umbral
    DEBIL     : una sola compra aislada de un director u otro cargo
    10%-OWNER : solo compras de accionistas >=10% (fondos, familias): otra señal, no "insider"
    NINGUNA   : sin compras P en la ventana
    N/A       : el emisor no reporta Form 4 (por ejemplo, emisores extranjeros con 20-F)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta

import edgar

MONTHS_BACK = 6
CLUSTER_WINDOW_DAYS = 30
CLEVEL_SINGLE_MIN_USD = 100_000


@dataclass
class Purchase:
    insider: str
    role: str          # CEO / CFO / Chair / Officer / Director / 10%-owner / Other
    title: str         # cargo textual del Form 4
    date: date
    shares: float
    price: float | None
    usd: float | None
    indirect: bool
    plan_10b5_1: bool
    accession: str


@dataclass
class InsiderReport:
    ticker: str
    status: str                       # ver docstring
    purchases: list[Purchase] = field(default_factory=list)
    clusters: list[list[Purchase]] = field(default_factory=list)
    note: str = ""
    filings_scanned: int = 0

    @property
    def total_usd(self) -> float:
        return sum(p.usd or 0 for p in self.purchases)

    def short(self) -> str:
        """Celda corta para la tabla resumen."""
        if self.status in ("N/A", "NINGUNA"):
            return self.status
        n_ins = len({p.insider for p in self.purchases})
        roles = sorted({p.role for p in self.purchases}, key=_role_rank)
        return f"{self.status}: {len(self.purchases)} compras, {n_ins} insiders ({'/'.join(roles)}), ${_fmt(self.total_usd)}"


def _fmt(x: float) -> str:
    if x >= 1e9:
        return f"{x/1e9:.2f}B"
    if x >= 1e6:
        return f"{x/1e6:.1f}M"
    if x >= 1e3:
        return f"{x/1e3:.0f}K"
    return f"{x:.0f}"


_ROLE_ORDER = {"CEO": 0, "CFO": 1, "Chair": 2, "Officer": 3, "Director": 4, "10%-owner": 5, "Other": 6}


def _role_rank(r: str) -> int:
    return _ROLE_ORDER.get(r, 9)


def _classify_role(rel: ET.Element | None) -> tuple[str, str]:
    if rel is None:
        return "Other", ""
    title = (rel.findtext("officerTitle") or "").strip()
    is_dir = (rel.findtext("isDirector") or "0").strip() in ("1", "true")
    is_off = (rel.findtext("isOfficer") or "0").strip() in ("1", "true")
    is_ten = (rel.findtext("isTenPercentOwner") or "0").strip() in ("1", "true")
    t = title.lower()
    if is_off and re.search(r"chief executive|\bceo\b", t):
        return "CEO", title
    if is_off and re.search(r"chief financial|\bcfo\b", t):
        return "CFO", title
    if re.search(r"chair", t):
        return "Chair", title or "Chair"
    if is_off:
        return "Officer", title
    if is_dir:
        return "Director", title or "Director"
    if is_ten:
        return "10%-owner", title or "10% owner"
    return "Other", title


def _val(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    v = el.find(path)
    if v is None:
        return None
    inner = v.find("value")
    return (inner.text if inner is not None else v.text) or None


def _parse_form4(xml_bytes: bytes, accession: str) -> list[Purchase]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    owners = []
    for ro in root.findall("reportingOwner"):
        name = (ro.findtext("reportingOwnerId/rptOwnerName") or "?").strip()
        role, title = _classify_role(ro.find("reportingOwnerRelationship"))
        owners.append((name, role, title))
    if not owners:
        return []
    plan = (root.findtext("aff10b5One") or "0").strip() in ("1", "true")
    footnotes = " ".join((f.text or "") for f in root.findall("footnotes/footnote")).lower()
    # Solo cuenta si la nota dice que la compra fue *bajo* un plan, no si lo menciona de paso.
    plan = plan or bool(re.search(r"(pursuant to|under|in accordance with)[^.]{0,60}10b5-1", footnotes))

    out = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _val(tx, "transactionCoding/transactionCode")
        ad = _val(tx, "transactionAmounts/transactionAcquiredDisposedCode")
        if code != "P" or ad != "A":
            continue
        d = _val(tx, "transactionDate")
        shares = _val(tx, "transactionAmounts/transactionShares")
        price = _val(tx, "transactionAmounts/transactionPricePerShare")
        own = _val(tx, "ownershipNature/directOrIndirectOwnership")
        try:
            dt = date.fromisoformat(d[:10]) if d else None
            sh = float(shares) if shares else 0.0
            pr = float(price) if price else None
        except ValueError:
            continue
        if not dt or sh <= 0:
            continue
        # Un Form 4 puede tener varios reporting owners (p. ej. un director y su trust).
        # Se atribuye al primero, que es el insider principal.
        name, role, title = owners[0]
        out.append(
            Purchase(
                insider=name, role=role, title=title, date=dt, shares=sh, price=pr,
                usd=(sh * pr) if pr else None, indirect=(own == "I"), plan_10b5_1=plan, accession=accession,
            )
        )
    return out


def _find_clusters(purchases: list[Purchase]) -> list[list[Purchase]]:
    """Grupos de compras de >=2 insiders distintos dentro de una ventana de 30 días."""
    ps = sorted(purchases, key=lambda p: p.date)
    clusters: list[list[Purchase]] = []
    used: set[int] = set()
    for i, p in enumerate(ps):
        if i in used:
            continue
        group = [q for q in ps if 0 <= (q.date - p.date).days <= CLUSTER_WINDOW_DAYS]
        if len({q.insider for q in group}) >= 2:
            clusters.append(group)
            used.update(ps.index(q) for q in group)
    return clusters


def analyze(ticker: str, months_back: int = MONTHS_BACK) -> InsiderReport:
    rep = InsiderReport(ticker=ticker.upper(), status="N/A")
    cik = edgar.ticker_to_cik(ticker)
    if not cik:
        rep.note = "sin CIK en la SEC"
        return rep
    try:
        filings = edgar.recent_filings(cik, {"4", "4/A"})
    except Exception as e:  # noqa: BLE001
        rep.note = f"error EDGAR: {str(e)[:80]}"
        return rep
    # ¿El emisor reporta 20-F/40-F? Entonces sus insiders no presentan Form 4.
    if not filings:
        others = edgar.recent_filings(cik, {"20-F", "40-F"})
        rep.note = "emisor extranjero (20-F/40-F): insiders no presentan Form 4" if others else "sin Form 4 en EDGAR"
        return rep

    cutoff = date.today() - timedelta(days=30 * months_back + 5)
    recent = [f for f in filings if date.fromisoformat(f["filed"]) >= cutoff]
    rep.filings_scanned = len(recent)
    seen: set[tuple] = set()
    for f in recent:
        cik_int = int(cik)
        doc = f["primary_doc"].split("/")[-1]  # "xslF345X06/form4.xml" -> "form4.xml"
        url = f"{edgar.filing_dir(cik_int, f['accession'])}/{doc}"
        try:
            xml = edgar.get(url)
            if b"<ownershipDocument" not in xml:
                # buscar el xml real en el índice del filing
                items = edgar.filing_index(cik_int, f["accession"])
                xmls = [i["name"] for i in items if i["name"].lower().endswith(".xml")]
                if not xmls:
                    continue
                xml = edgar.get(f"{edgar.filing_dir(cik_int, f['accession'])}/{xmls[0]}")
        except Exception:  # noqa: BLE001
            continue
        for p in _parse_form4(xml, f["accession"]):
            key = (p.insider, p.date, round(p.shares))
            if key in seen:  # 4/A duplicando un 4 (a veces con precio corregido)
                continue
            seen.add(key)
            if p.date >= cutoff:
                rep.purchases.append(p)

    if not rep.purchases:
        rep.status = "NINGUNA"
        rep.note = f"{rep.filings_scanned} Form 4 revisados, sin compras con código P"
        return rep

    non_owner = [p for p in rep.purchases if p.role != "10%-owner"]
    # Los accionistas >=10% (empresas, fondos) no cuentan para el patrón de grupo:
    # que Volkswagen compre Rivian no es "varios insiders comprando".
    rep.clusters = _find_clusters(non_owner)
    roles = {p.role for p in non_owner}
    n_insiders = len({p.insider for p in non_owner})

    if not non_owner:
        rep.status = "10%-OWNER"
        rep.note = "solo compras de accionistas >=10% (fondos/familias), no de ejecutivos ni directores"
    elif any(len({q.insider for q in c}) >= 3 for c in rep.clusters) or (
        rep.clusters and any(p.role in ("CEO", "CFO") for c in rep.clusters for p in c)
    ):
        rep.status = "FUERTE"
        rep.note = "varios insiders comprando en la misma ventana de 30 días, incluye CEO/CFO o >=3 personas"
    elif ({"CEO", "CFO"} & roles) and any(
        (p.usd or 0) >= CLEVEL_SINGLE_MIN_USD for p in non_owner if p.role in ("CEO", "CFO")
    ):
        rep.status = "MODERADA"
        rep.note = f"compra individual de CEO/CFO >= ${CLEVEL_SINGLE_MIN_USD:,}"
    elif rep.clusters:
        rep.status = "MODERADA"
        rep.note = "varios insiders (sin CEO/CFO) comprando en la misma ventana de 30 días"
    else:
        rep.status = "DEBIL"
        rep.note = f"compra(s) aislada(s) de {n_insiders} insider(s) sin patrón de grupo"

    if any(p.plan_10b5_1 for p in rep.purchases):
        rep.note += "; alguna compra bajo plan 10b5-1 (preprogramada, menos informativa)"
    if any(p.indirect for p in rep.purchases):
        rep.note += "; alguna compra indirecta (trust/familiar)"
    return rep


def to_markdown(rep: InsiderReport) -> str:
    lines = [f"**Insiders (Form 4, últimos {MONTHS_BACK} meses):** `{rep.status}` — {rep.note}"]
    if rep.purchases:
        lines.append("")
        lines.append("| Fecha | Insider | Cargo | Acciones | Precio | USD | Notas |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in sorted(rep.purchases, key=lambda p: p.date, reverse=True):
            notes = []
            if p.indirect:
                notes.append("indirecta")
            if p.plan_10b5_1:
                notes.append("10b5-1")
            in_cluster = any(p in c for c in rep.clusters)
            if in_cluster:
                notes.append("en grupo")
            lines.append(
                f"| {p.date} | {p.insider} | {p.role}: {p.title[:30]} | {p.shares:,.0f} | "
                f"{('$' + format(p.price, ',.2f')) if p.price else 'n/d'} | "
                f"{('$' + _fmt(p.usd)) if p.usd else 'n/d'} | {', '.join(notes)} |"
            )
    return "\n".join(lines)
