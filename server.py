"""
UI web local del screener.

Uso:
    python server.py            # abre http://127.0.0.1:8765

Reutiliza screener.py, insiders.py, institutions.py y bottlenecks.py. Solo escucha en
127.0.0.1. No es una recomendación de compra.
"""

from __future__ import annotations

import math
import os
import re
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from tabulate import tabulate

import bottlenecks
import edgar
import insiders
import institutions
import screener

# Por defecto solo localhost. En Docker se usa SCREENER_HOST=0.0.0.0 dentro del contenedor y el
# puerto se publica únicamente en 127.0.0.1 del anfitrión (ver docker-compose.yml).
HOST = os.environ.get("SCREENER_HOST", "127.0.0.1")
PORT = int(os.environ.get("SCREENER_PORT", "8765"))
WEB = Path(__file__).with_name("web")
MAX_TICKERS = 40
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.\-][A-Z0-9]{1,3})?$")
DEFAULT_UA = edgar.HEADERS["User-Agent"]

# clave de la UI -> (objeto, nombre, factor UI->código, tipo)
_T = screener.T
TH_SPEC = {
    "f1_mop": (_T, "op_margin_min", 0.01, float),
    "f1_mb": (_T, "gross_margin_path_min", 0.01, float),
    "f2_w": (_T, "customer_warn_pct", 1, float),
    "f2_f": (_T, "customer_fail_pct", 1, float),
    "f3_mop": (_T, "moat_avg_op_margin_min", 0.01, float),
    "f3_cv": (_T, "moat_margin_cv_max", 1, float),
    "f3_mb": (_T, "moat_gross_margin_min", 0.01, float),
    "f3_roe": (_T, "moat_roe_min", 0.01, float),
    "f4_y": (_T, "fcf_min_years", 1, int),
    "f4_m": (_T, "fcf_margin_min", 0.01, float),
    "f5_p": (_T, "peg_pass", 1, float),
    "f5_w": (_T, "peg_warn", 1, float),
    "f5_ev": (_T, "ev_ebitda_cheap", 1, float),
    "f5_bad": (_T, "ev_ebitda_bad", 1, float),
    "f5_cap": (_T, "growth_cap", 0.01, float),
    "MONTHS_BACK": (insiders, "MONTHS_BACK", 1, int),
    "CLUSTER_WINDOW_DAYS": (insiders, "CLUSTER_WINDOW_DAYS", 1, int),
    "CLEVEL_SINGLE_MIN_USD": (insiders, "CLEVEL_SINGLE_MIN_USD", 1, int),
    "STALE_DAYS": (institutions, "STALE_DAYS", 1, int),
    "MAX_SENTENCES": (bottlenecks, "MAX_SENTENCES", 1, int),
}


def _get(obj, name):
    return obj[name] if isinstance(obj, dict) else getattr(obj, name)


def _set(obj, name, val):
    if isinstance(obj, dict):
        obj[name] = val
    else:
        setattr(obj, name, val)


def _num(x: float) -> float | int:
    x = round(x, 6)
    return int(x) if float(x).is_integer() else x


# valores por defecto del código, en unidades de la UI (se capturan una sola vez al arrancar)
DEFAULTS = {k: _num(_get(o, n) / f) for k, (o, n, f, _t) in TH_SPEC.items()}

app = Flask(__name__, static_folder=None)

LOCK = threading.Lock()
RUN: dict = {"running": False, "step": 0, "total": 0, "msg": "", "error": ""}
DATA: dict | None = None
FRAMES: dict = {}


# ----------------------------------------------------------------------------
# Seguridad mínima: solo localhost, y los POST solo con JSON del mismo origen
# ----------------------------------------------------------------------------
@app.before_request
def _guard():
    host = (request.host or "").rsplit(":", 1)[0].strip("[]")
    if host not in ("127.0.0.1", "localhost"):
        abort(403)
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != request.host:
            abort(403)
        if not request.is_json:
            abort(415)


@app.after_request
def _headers(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ----------------------------------------------------------------------------
# Serialización
# ----------------------------------------------------------------------------
def _fmt_usd(x: float) -> str:
    for unit, div, nd in (("B", 1e9, 2), ("M", 1e6, 1), ("K", 1e3, 0)):
        if abs(x) >= div:
            return f"{x/div:.{nd}f}{unit}"
    return f"{x:.0f}"


def _split_quote(note: str) -> tuple[str, str]:
    m = re.search(r"«(.*)»\s*$", note, re.S)
    if not m:
        return note, ""
    return note[: m.start()].rstrip(" ."), m.group(1).strip()


def _ins_row(rep) -> str:
    if rep.status == "NINGUNA":
        return f"{rep.filings_scanned} Form 4, sin código P"
    if rep.status == "N/A":
        return "Emisor extranjero (20-F), sin Form 4" if "20-F" in rep.note else (rep.note or "sin datos")
    n = len(rep.purchases)
    roles = sorted({p.role for p in rep.purchases}, key=insiders._role_rank)
    return f"{n} compra{'s' if n != 1 else ''} · {'/'.join(roles)} · ${_fmt_usd(rep.total_usd)}"


def _ins_payload(rep):
    if rep is None:
        return None
    buys = [
        {
            "d": p.date.isoformat(), "who": p.insider, "role": p.role, "title": p.title,
            "sh": f"{p.shares:,.0f}", "px": f"${p.price:,.2f}" if p.price else "n/d",
            "usd": f"${_fmt_usd(p.usd or 0)}",
            "tags": (["indirecta"] if p.indirect else []) + (["10b5-1"] if p.plan_10b5_1 else []),
        }
        for p in sorted(rep.purchases, key=lambda p: p.date, reverse=True)
    ]
    return {
        "label": rep.status, "note": rep.note, "row": _ins_row(rep),
        "total": f"${_fmt_usd(rep.total_usd)}" if rep.purchases else "", "buys": buys,
    }


def _inst_payload(rep):
    if rep is None:
        return None
    if rep.cusip is None:
        return {"na": True, "note": rep.note, "nums": [0, 0, 0, 0], "n_active": 0, "rows": []}
    sig = [p for p in rep.positions if p.group != "pasivo"]
    held = [p for p in sig if p.change != "SALIO"]
    up = sum(1 for p in held if p.change == "NUEVA" or p.change.startswith("+"))
    down = sum(1 for p in held if p.change.startswith("-"))
    out = sum(1 for p in sig if p.change == "SALIO")
    n_active = sum(1 for m in institutions._managers_loaded if m.group != "pasivo" and not m.error and not m.stale)
    rows = [
        {
            "g": p.manager, "grp": p.group, "sh": f"{p.shares:,.0f}", "val": f"{p.value_usd/1e6:,.0f}M",
            "pct": round(p.weight_pct, 2), "ch": p.change, "per": p.period.isoformat(),
        }
        for p in sig
    ]
    return {"na": False, "note": rep.note, "nums": [len(held), up, down, out], "n_active": n_active, "rows": rows}


def _bn_payload(rep):
    if rep is None:
        return None
    return {"src": "; ".join(rep.sources), "items": list(rep.sentences), "note": rep.note}


def _company(r) -> dict:
    if r.error:
        return {"t": r.ticker, "n": r.error, "s": "", "error": True}
    notes, quotes = [], []
    for f in screener.FILTERS:
        note, q = _split_quote(r.filters[f].note)
        notes.append(note)
        quotes.append(q)
    return {
        "t": r.ticker, "n": r.name, "s": r.sector, "error": False,
        "v": [r.filters[f].status for f in screener.FILTERS], "notes": notes, "q": quotes,
        "ins": _ins_payload(r.insiders), "f13": _inst_payload(r.inst), "bn": _bn_payload(r.bottlenecks),
    }


def _convergence_payload(results):
    by_sector: dict[str, list] = {}
    for r in results:
        if not r.error and r.sector:
            by_sector.setdefault(r.sector, []).append(r)
    sectors, single = [], []
    for sec, rs in by_sector.items():
        if len(rs) < 2:
            single.append({"sector": sec, "tickers": [r.ticker for r in rs]})
            continue
        tks = [r.ticker for r in rs]
        held: dict[str, dict] = {}
        for r in rs:
            if r.inst is None:
                continue
            for p in r.inst.positions:
                if p.group == "pasivo" or p.change == "SALIO":
                    continue
                m = held.setdefault(p.manager, {"g": p.manager, "grp": p.group, "held": [], "inc": []})
                m["held"].append(r.ticker)
                if p.change == "NUEVA" or p.change.startswith("+"):
                    m["inc"].append(r.ticker)
        managers = []
        for m in held.values():
            if len(m["held"]) < 2:
                continue
            managers.append(
                {
                    "g": m["g"], "grp": m["grp"],
                    "held": [t for t in tks if t in m["held"]],
                    "up": [t for t in tks if t in m["inc"]] if len(m["inc"]) >= 2 else [],
                }
            )
        sectors.append({"sector": sec, "tickers": tks, "managers": managers})
    return {"sectors": sectors, "single": single}


def _meta(results, req: dict, ran_at: str) -> dict:
    mods = req["modules"]
    meta = {
        "out": req["out"], "n": len(results), "ran_at": ran_at, "modules": mods, "th": req["th"],
        "keywords": bottlenecks.KEYWORDS, "period": None, "stale": [], "passive": [], "lag": "",
    }
    if mods["f13"]:
        ms = institutions._managers_loaded
        periods = sorted({m.latest_period for m in ms if m.latest_period and not m.error}, reverse=True)
        meta["period"] = periods[0].isoformat() if periods else None
        meta["stale"] = [m.name for m in ms if m.stale or m.error]
        meta["passive"] = [m.name for m in ms if m.group == "pasivo"]
        meta["lag"] = institutions.lag_disclaimer(ms)
    return meta


def _markdown(results, summary, meta) -> str:
    parts = ["# Screener — resumen\n\n", tabulate(summary, headers="keys", tablefmt="github", showindex=False), "\n\n"]
    if meta["lag"]:
        parts.append(f"> **Aviso 13F:** {meta['lag']}\n\n")
    parts.append("# Detalle\n\n")
    for r in results:
        parts.append(f"## {r.ticker} — {r.name}" + (f" [{r.sector}]" if r.sector else "") + "\n\n")
        if r.error:
            parts.append(f"ERROR: {r.error}\n\n")
            continue
        for f in screener.FILTERS:
            parts.append(f"- **{f}** `{r.filters[f].status}` — {r.filters[f].note}\n")
        parts.append("\n")
        if r.insiders is not None:
            parts.append(insiders.to_markdown(r.insiders) + "\n\n")
        if r.inst is not None:
            parts.append(institutions.to_markdown(r.inst) + "\n\n")
        if r.bottlenecks is not None:
            parts.append(bottlenecks.to_markdown(r.bottlenecks) + "\n\n")
    parts.append(
        "\n> Proyecto educativo e informativo. No es asesoría financiera ni una recomendación de "
        "comprar o vender ningún valor.\n"
    )
    return "".join(parts)


# ----------------------------------------------------------------------------
# Corrida
# ----------------------------------------------------------------------------
def _validate(body: dict) -> dict:
    tickers = []
    for t in body.get("tickers") or []:
        t = str(t).strip().upper()
        if not TICKER_RE.match(t):
            raise ValueError(f"ticker inválido: {t[:20]}")
        if t not in tickers:
            tickers.append(t)
    if not tickers:
        raise ValueError("indica al menos un ticker")
    if len(tickers) > MAX_TICKERS:
        raise ValueError(f"máximo {MAX_TICKERS} tickers por corrida")

    m = body.get("modules") or {}
    modules = {k: bool(m.get(k, True)) for k in ("ins", "f13", "bn")}

    th = {}
    given = body.get("th") or {}
    for key, (_o, _n, _f, typ) in TH_SPEC.items():
        raw = given.get(key, DEFAULTS[key])
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"umbral no numérico: {key}") from None
        if not math.isfinite(val) or val < 0 or val > 1e9:
            raise ValueError(f"umbral fuera de rango: {key}")
        th[key] = int(val) if typ is int else val

    out = re.sub(r"[^\w.\-]", "_", str(body.get("out") or "resultados").strip())[:40] or "resultados"
    ua = str(body.get("ua") or "").strip()
    if len(ua) > 200 or re.search(r"[\r\n\x00]", ua):
        raise ValueError("User-Agent inválido")
    return {"tickers": tickers, "modules": modules, "th": th, "out": out, "ua": ua}


def _apply(req: dict) -> None:
    for key, (obj, name, factor, typ) in TH_SPEC.items():
        val = req["th"][key] * factor
        _set(obj, name, int(round(val)) if typ is int else float(val))
    edgar.HEADERS["User-Agent"] = req["ua"] or DEFAULT_UA
    # el estado "desactualizado" de cada gestor depende de STALE_DAYS
    for m in institutions._managers_loaded:
        if m.latest_period and not m.error:
            m.stale = (datetime.now().date() - m.latest_period).days > institutions.STALE_DAYS


def _progress(step: int, total: int, msg: str) -> None:
    RUN.update(step=step, total=total, msg=msg)


def _do_run(req: dict) -> None:
    global DATA, FRAMES
    try:
        mods = req["modules"]
        tickers = req["tickers"]
        _apply(req)
        total = len(tickers) * (1 + mods["ins"] + mods["f13"] + mods["bn"]) + int(mods["f13"])
        step = 0
        results = []
        for tk in tickers:
            results.append(screener.screen(tk, use_sec=True))
            step += 1
            _progress(step, total, f"{tk} · fundamentales")

        if mods["ins"]:
            for r in results:
                if not r.error:
                    try:
                        r.insiders = insiders.analyze(r.ticker, months_back=insiders.MONTHS_BACK)
                    except Exception as e:  # noqa: BLE001
                        r.insiders = insiders.InsiderReport(ticker=r.ticker, status="N/A", note=f"error: {str(e)[:80]}")
                step += 1
                _progress(step, total, f"{r.ticker} · Form 4")

        if mods["f13"]:
            _progress(step, total, "cargando 13F de gestores (la primera vez tarda ~4 min)")
            managers = institutions.load_managers(progress=lambda s: RUN.update(msg=s))
            _apply(req)  # recalcula "desactualizado" con los gestores ya cargados
            step += 1
            for r in results:
                if not r.error:
                    try:
                        r.inst = institutions.analyze(r.ticker, managers)
                    except Exception as e:  # noqa: BLE001
                        r.inst = institutions.InstReport(ticker=r.ticker, cusip=None, note=f"error: {str(e)[:80]}")
                step += 1
                _progress(step, total, f"{r.ticker} · 13F")

        if mods["bn"]:
            for r in results:
                if not r.error:
                    try:
                        r.bottlenecks = bottlenecks.analyze(r.ticker)
                    except Exception as e:  # noqa: BLE001
                        r.bottlenecks = bottlenecks.BottleneckReport(ticker=r.ticker, note=f"error: {str(e)[:80]}")
                step += 1
                _progress(step, total, f"{r.ticker} · cuellos de botella")

        summary, detail = screener.to_frames(results)
        meta = _meta(results, req, datetime.now().isoformat(timespec="seconds"))
        DATA = {
            "meta": meta,
            "companies": [_company(r) for r in results],
            "convergence": _convergence_payload(results) if mods["f13"] else None,
        }
        FRAMES = {"summary": summary, "detail": detail, "md": _markdown(results, summary, meta)}
        RUN.update(error="")
    except Exception as e:  # noqa: BLE001
        RUN.update(error=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        RUN["running"] = False


# ----------------------------------------------------------------------------
# Rutas
# ----------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(WEB, name)


@app.get("/api/state")
def api_state():
    return jsonify({"data": DATA, "run": RUN, "defaults": DEFAULTS, "ua_env": os.environ.get("SEC_USER_AGENT", "")})


@app.get("/api/progress")
def api_progress():
    return jsonify(RUN)


@app.post("/api/run")
def api_run():
    try:
        req = _validate(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    with LOCK:
        if RUN["running"]:
            return jsonify({"error": "ya hay una corrida en curso"}), 409
        RUN.update(running=True, step=0, total=1, msg="iniciando", error="")
    threading.Thread(target=_do_run, args=(req,), daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/download/<kind>")
def api_download(kind):
    if not DATA or kind not in ("resumen", "detalle", "md"):
        abort(404)
    out = DATA["meta"]["out"]
    if kind == "md":
        body, mime, name = FRAMES["md"].encode("utf-8"), "text/markdown", f"{out}.md"
    else:
        df = FRAMES["summary" if kind == "resumen" else "detail"]
        body = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        mime, name = "text/csv", f"{out}_{kind}.csv"
    return Response(body, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


if __name__ == "__main__":
    url = f"http://{'127.0.0.1' if HOST == '0.0.0.0' else HOST}:{PORT}"
    print(f"Screener UI en {url}  (Ctrl+C para salir)")
    if not os.environ.get("SCREENER_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, threaded=True)
