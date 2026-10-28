"""
Cliente compartido para SEC EDGAR: límite de velocidad (la SEC permite ~10 req/s,
usamos 5), reintentos y caché en disco para no descargar dos veces lo mismo.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path

SEC_UA = os.environ.get("SEC_USER_AGENT", "screener-personal contacto@example.com")
HEADERS = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}
CACHE_DIR = Path(__file__).with_name(".cache_edgar")
MIN_INTERVAL = 0.2  # segundos entre peticiones (5 req/s)
_last_call = 0.0


def get(url: str, retries: int = 4, cache: bool = True, ttl_days: float | None = None) -> bytes:
    """GET con throttle + caché. `ttl_days=None` = caché permanente (filings no cambian)."""
    global _last_call
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE_DIR / key
    if cache and path.exists():
        if ttl_days is None or (time.time() - path.stat().st_mtime) < ttl_days * 86400:
            return path.read_bytes()

    last: Exception | None = None
    for i in range(retries):
        wait = MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            _last_call = time.time()
            if cache:
                CACHE_DIR.mkdir(exist_ok=True)
                path.write_bytes(data)
            return data
        except Exception as e:  # noqa: BLE001
            _last_call = time.time()
            last = e
            code = getattr(e, "code", None)
            if code == 404:
                break
            time.sleep(2.0 * (i + 1))  # 429/503: esperar más
    raise RuntimeError(f"EDGAR {url}: {last}")


def get_json(url: str, **kw) -> dict:
    return json.loads(get(url, **kw))


def submissions(cik: str | int) -> dict:
    """Índice de filings de una entidad (se refresca cada día)."""
    return get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", ttl_days=1)


def recent_filings(cik: str | int, forms: set[str]) -> list[dict]:
    """Lista de filings recientes (hasta 1000) filtrados por tipo de formulario."""
    sub = submissions(cik)
    r = sub["filings"]["recent"]
    out = []
    for i in range(len(r["form"])):
        if r["form"][i] in forms:
            out.append(
                {
                    "form": r["form"][i],
                    "accession": r["accessionNumber"][i],
                    "filed": r["filingDate"][i],
                    "report_date": r["reportDate"][i],
                    "primary_doc": r["primaryDocument"][i],
                    "cik": int(sub["cik"]),
                    "entity": sub.get("name", ""),
                }
            )
    return out


def filing_dir(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"


def filing_index(cik: int, accession: str) -> list[dict]:
    idx = get_json(filing_dir(cik, accession) + "/index.json")
    return idx["directory"]["item"]


_cik_map: dict[str, str] | None = None


def ticker_to_cik(ticker: str) -> str | None:
    global _cik_map
    if _cik_map is None:
        data = get_json("https://www.sec.gov/files/company_tickers.json", ttl_days=7)
        _cik_map = {v["ticker"].upper(): f"{int(v['cik_str']):010d}" for v in data.values()}
    tk = ticker.upper()
    for cand in (tk, tk.replace("-", "."), tk.replace(".", "-"), tk.replace("-", "")):
        if cand in _cik_map:
            return _cik_map[cand]
    return None


def find_entity_cik(name: str, form_type: str = "13F-HR") -> tuple[str, str] | None:
    """Busca una entidad por nombre en EDGAR. Devuelve (cik, nombre) si hay
    coincidencia única, si no la primera de la lista."""
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?company="
        + urllib.request.quote(name)
        + f"&type={form_type}&output=atom"
    )
    atom = get(url, ttl_days=30).decode("utf-8", "ignore")
    single = re.search(r"<cik>(\d+)</cik>.*?<conformed-name>([^<]+)</conformed-name>", atom, re.S)
    if single:
        return single.group(1).zfill(10), single.group(2).replace("&amp;", "&")
    multi = re.findall(r"<entry>.*?<title>([^<]+)</title>.*?cik-(\d+)", atom, re.S)
    if multi:
        return multi[0][1].zfill(10), multi[0][0].replace("&amp;", "&")
    return None


def html_to_text(raw: bytes | str) -> str:
    import html as _html

    s = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s)
