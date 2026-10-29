"""
Pruebas del módulo LSE sin red: las respuestas HTTP se simulan con datos sintéticos
(inventados, no son datos de LSE).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import csv
import email.message
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lse  # noqa: E402

KEY = "clave-de-prueba-123456"
HEADER = ["id", "symbol", "report_type", "date", "fiscal_year", "period", "reported_currency", "data", "filing_date", "accepted_date"]


def row(kind, d, data, period="FY", cur="USD", filed="2025-03-01"):
    return ["1", "TEST", kind, d, d[:4], period, cur, json.dumps(data), filed, filed]


def make_csv(rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    return buf.getvalue()


def sample_csv(op_margin=0.15, cur="USD") -> str:
    """Tres años fiscales con ingresos 1000 y margen operativo `op_margin`; FCF = 100 - 30 = 70 (7%)."""
    rows = []
    for y in (2022, 2023, 2024):
        d = f"{y}-12-31"
        rows.append(row("income", d, {"revenue": 1000, "operatingIncome": 1000 * op_margin}, cur=cur))
        rows.append(row("cashflow", d, {"freeCashFlow": 999, "operatingCashFlow": 100, "investmentsInPropertyPlantAndEquipment": -30}, cur=cur))
    return make_csv(rows)


class Resp:
    def __init__(self, text: str):
        self._b = text.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    h = email.message.Message()
    if retry_after is not None:
        h["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://example.invalid", code, "x", h, None)


def yf_series(op, fcf, years=(2022, 2023, 2024)):
    return ({date(y, 12, 31): op for y in years}, {date(y, 12, 31): fcf for y in years})


class Base(unittest.TestCase):
    def setUp(self):
        lse.reset_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (
            mock.patch.object(lse, "CACHE_DIR", Path(self.tmp.name)),
            mock.patch.dict(os.environ, {"LSE_KEY": KEY}),
            mock.patch("lse.time.sleep"),
        ):
            p.start()
            self.addCleanup(p.stop)


class ParseTests(Base):
    def test_only_fy_and_latest_filing_wins(self):
        text = make_csv([
            row("income", "2024-12-31", {"revenue": 1000}, filed="2025-02-01"),
            row("income", "2024-12-31", {"revenue": 1100}, filed="2025-04-01"),  # reexpresión
            row("income", "2024-09-30", {"revenue": 250}, period="Q3"),
            row("income", "2024-12-31", {"revenue": 1200}, period="TTM"),
            row("ratios", "2024-12-31", {"x": 1}),
        ])
        annual = lse.parse_annual(text)
        self.assertEqual(list(annual), [date(2024, 12, 31)])
        self.assertEqual(annual[date(2024, 12, 31)].income["revenue"], 1100)

    def test_old_cashflow_zeros_are_missing(self):
        text = make_csv([
            row("cashflow", "2008-12-31", {"operatingCashFlow": 0, "freeCashFlow": 0}),
            row("cashflow", "2012-12-31", {"operatingCashFlow": 0, "freeCashFlow": 5}),
        ])
        annual = lse.parse_annual(text)
        self.assertIsNone(annual[date(2008, 12, 31)].cashflow["operatingCashFlow"])
        self.assertEqual(annual[date(2012, 12, 31)].cashflow["operatingCashFlow"], 0)  # >= 2011: cero real

    def test_bad_rows_are_skipped(self):
        text = make_csv([["1", "T", "income", "2024-12-31", "2024", "FY", "USD", "{no es json", "", ""]])
        self.assertEqual(lse.parse_annual(text), {})

    def test_fcf_uses_investments_not_free_cash_flow_field(self):
        fy = lse.parse_annual(sample_csv())[date(2024, 12, 31)]
        self.assertAlmostEqual(lse.fcf_margin(fy), 0.07)  # (100-30)/1000, no 999/1000
        self.assertAlmostEqual(lse.op_margin(fy), 0.15)


class CompareTests(Base):
    def test_gap_over_3pp_adds_note(self):
        annual = lse.parse_annual(sample_csv(op_margin=0.15))
        op, fcf = yf_series(0.087, 0.07)
        chk = lse.compare(annual, op, fcf)
        self.assertTrue(chk.ok)
        self.assertIn("LSE difiere de yfinance", chk.op_note)
        self.assertIn("15.0%", chk.op_note)
        self.assertIn("8.7%", chk.op_note)
        self.assertEqual(chk.fcf_note, "")

    def test_gap_within_3pp_adds_nothing(self):
        annual = lse.parse_annual(sample_csv(op_margin=0.15))
        chk = lse.compare(annual, *yf_series(0.13, 0.07))
        self.assertEqual((chk.op_note, chk.fcf_note), ("", ""))

    def test_fcf_gap_note(self):
        annual = lse.parse_annual(sample_csv())
        chk = lse.compare(annual, *yf_series(0.15, 0.02))
        self.assertEqual(chk.op_note, "")
        self.assertIn("margen FCF", chk.fcf_note)

    def test_fiscal_year_end_offset_still_matches(self):
        annual = lse.parse_annual(make_csv([row("income", "2025-02-01", {"revenue": 1000, "operatingIncome": 200})]))
        chk = lse.compare(annual, {date(2025, 1, 31): 0.05}, {})
        self.assertTrue(chk.ok)
        self.assertIn("LSE difiere", chk.op_note)

    def test_no_common_years_is_na(self):
        annual = lse.parse_annual(sample_csv())
        chk = lse.compare(annual, {date(2010, 12, 31): 0.1}, {})
        self.assertFalse(chk.ok)
        self.assertIn("sin años fiscales en común", chk.na_note)

    def test_non_usd_currency_is_mentioned(self):
        annual = lse.parse_annual(sample_csv(op_margin=0.30, cur="EUR"))
        chk = lse.compare(annual, *yf_series(0.10, 0.07))
        self.assertIn("LSE reporta en EUR", chk.op_note)


class FetchTests(Base):
    def test_no_key_means_inactive(self):
        os.environ.pop("LSE_KEY")
        self.assertFalse(lse.available())
        with mock.patch("lse.urllib.request.urlopen") as m:
            chk = lse.check("CVX", *yf_series(0.1, 0.1))
        m.assert_not_called()
        self.assertFalse(chk.ok)
        self.assertIn("LSE_KEY", chk.na_note)

    def test_429_waits_retry_after_then_succeeds(self):
        with mock.patch("lse.urllib.request.urlopen", side_effect=[http_error(429, 7), Resp(sample_csv())]) as m:
            chk = lse.check("CVX", *yf_series(0.087, 0.07))
        self.assertTrue(chk.ok)
        self.assertEqual(m.call_count, 2)
        lse.time.sleep.assert_any_call(7.0)
        req = m.call_args[0][0]
        self.assertEqual(req.get_header("X-api-key"), KEY)
        self.assertNotIn(KEY, req.full_url)  # la clave va en el header, no en la URL
        self.assertIn("dataset=financial_reports", req.full_url)
        self.assertIn("from=2016-01-01", req.full_url)

    def test_429_gives_up_after_max_retries(self):
        with mock.patch("lse.urllib.request.urlopen", side_effect=[http_error(429, 1)] * (lse.MAX_RETRIES + 1)) as m:
            chk = lse.check("CVX", *yf_series(0.1, 0.1))
        self.assertFalse(chk.ok)
        self.assertEqual(m.call_count, lse.MAX_RETRIES + 1)

    def test_401_is_na_blocks_next_calls_and_never_leaks_key(self):
        with mock.patch("lse.urllib.request.urlopen", side_effect=http_error(401)) as m:
            a = lse.check("CVX", *yf_series(0.1, 0.1))
            b = lse.check("ASML", *yf_series(0.1, 0.1))
        self.assertEqual(m.call_count, 1)  # no insiste con una clave inválida
        for chk in (a, b):
            self.assertFalse(chk.ok)
            self.assertIn("401", chk.na_note)
            self.assertNotIn(KEY, chk.na_note)

    def test_403_and_404_are_na_without_raising(self):
        for code in (403, 404):
            lse.reset_state()
            with mock.patch("lse.urllib.request.urlopen", side_effect=http_error(code)):
                chk = lse.check("RIVN", *yf_series(0.1, 0.1))
            self.assertFalse(chk.ok)
            self.assertIn(str(code), chk.na_note)

    def test_dash_ticker_retries_with_dot(self):
        with mock.patch("lse.urllib.request.urlopen", side_effect=[http_error(404), Resp(sample_csv())]) as m:
            chk = lse.check("BRK-B", *yf_series(0.15, 0.07))
        self.assertTrue(chk.ok)
        urls = [c[0][0].full_url for c in m.call_args_list]
        self.assertIn("symbol=BRK-B", urls[0])
        self.assertIn("symbol=BRK.B", urls[1])

    def test_empty_response_is_not_available(self):
        with mock.patch("lse.urllib.request.urlopen", return_value=Resp(",".join(HEADER) + "\n")):
            chk = lse.check("ZZZZ", *yf_series(0.1, 0.1))
        self.assertFalse(chk.ok)
        self.assertIn("404", chk.na_note)

    def test_network_error_is_na(self):
        with mock.patch("lse.urllib.request.urlopen", side_effect=urllib.error.URLError("sin red")):
            chk = lse.check("CVX", *yf_series(0.1, 0.1))
        self.assertFalse(chk.ok)
        self.assertIn("sin conexión", chk.na_note)

    def test_cache_hit_then_refresh_after_ttl(self):
        with mock.patch("lse.urllib.request.urlopen", return_value=Resp(sample_csv())) as m:
            lse.check("CVX", *yf_series(0.15, 0.07))
            lse.check("CVX", *yf_series(0.15, 0.07))
            self.assertEqual(m.call_count, 1)  # segunda vez: caché
            old = time.time() - (lse.CACHE_TTL_DAYS + 1) * 86400
            os.utime(lse._cache_path("CVX"), (old, old))
            lse.check("CVX", *yf_series(0.15, 0.07))
            self.assertEqual(m.call_count, 2)  # vencida: se descarga de nuevo


class ScreenerHookTests(Base):
    """El cruce con LSE solo agrega notas: nunca cambia un status."""

    def setUp(self):
        super().setUp()
        import pandas as pd
        import screener

        self.pd, self.screener = pd, screener
        cols = [pd.Timestamp(f"{y}-12-31") for y in (2024, 2023, 2022)]
        self.fin = pd.DataFrame(
            {c: [1000.0, 87.0] for c in cols}, index=["Total Revenue", "Operating Income"]
        )
        self.cf = pd.DataFrame({c: [70.0] for c in cols}, index=["Free Cash Flow"])

    def _result(self):
        r = self.screener.CompanyResult(ticker="TEST")
        for f in self.screener.FILTERS:
            r.filters[f] = self.screener.FilterResult("PASS", "nota original")
        return r

    def test_yf_margin_series(self):
        op, fm = self.screener._yf_margin_series(self.fin, self.cf)
        self.assertAlmostEqual(op[date(2024, 12, 31)], 0.087)
        self.assertAlmostEqual(fm[date(2024, 12, 31)], 0.07)

    def test_divergence_appends_notes_without_changing_status(self):
        r = self._result()
        with mock.patch("lse.urllib.request.urlopen", return_value=Resp(sample_csv(op_margin=0.15))):
            self.screener._append_lse_notes(r, "TEST", self.fin, self.cf)
        self.assertTrue(all(fr.status == "PASS" for fr in r.filters.values()))
        for f in ("1 Rentabilidad", "3 Moat"):
            self.assertIn("LSE difiere de yfinance", r.filters[f].note)
            self.assertTrue(r.filters[f].note.startswith("nota original"))
        self.assertEqual(r.filters["4 FCF"].note, "nota original")  # FCF coincide
        self.assertEqual(r.filters["2 Clientes"].note, "nota original")
        self.assertEqual(r.filters["5 Valuación"].note, "nota original")

    def test_lse_failure_marks_na_note_and_keeps_run_alive(self):
        r = self._result()
        with mock.patch("lse.urllib.request.urlopen", side_effect=http_error(404)):
            self.screener._append_lse_notes(r, "TEST", self.fin, self.cf)
        self.assertTrue(all(fr.status == "PASS" for fr in r.filters.values()))
        for f in ("1 Rentabilidad", "3 Moat", "4 FCF"):
            self.assertIn("LSE N/A", r.filters[f].note)
        self.assertEqual(r.filters["2 Clientes"].note, "nota original")

    def test_unexpected_error_inside_lse_is_contained(self):
        r = self._result()
        with mock.patch("lse.check", side_effect=RuntimeError("boom")):
            self.screener._append_lse_notes(r, "TEST", self.fin, self.cf)
        self.assertIn("LSE N/A", r.filters["1 Rentabilidad"].note)
        self.assertNotIn("boom", r.filters["1 Rentabilidad"].note)

    def test_screen_without_key_never_touches_lse(self):
        os.environ.pop("LSE_KEY")
        info = {"longName": "Test Co", "sector": "Technology"}
        fake = mock.Mock(info=info, financials=self.fin, cashflow=self.cf)
        with mock.patch.object(self.screener.yf, "Ticker", return_value=fake), \
                mock.patch("lse.urllib.request.urlopen") as net, \
                mock.patch.object(self.screener, "f2_customers", return_value=self.screener.FilterResult("N/A", "x")):
            r = self.screener.screen("TEST", use_sec=False)
        net.assert_not_called()
        self.assertEqual(r.error, "")
        self.assertNotIn("LSE", " ".join(fr.note for fr in r.filters.values()))

    def test_no_lse_flag_skips_even_with_key(self):
        info = {"longName": "Test Co", "sector": "Technology"}
        fake = mock.Mock(info=info, financials=self.fin, cashflow=self.cf)
        with mock.patch.object(self.screener.yf, "Ticker", return_value=fake), \
                mock.patch("lse.urllib.request.urlopen") as net, \
                mock.patch.object(self.screener, "f2_customers", return_value=self.screener.FilterResult("N/A", "x")):
            r = self.screener.screen("TEST", use_sec=False, use_lse=False)
        net.assert_not_called()
        self.assertEqual(r.error, "")


if __name__ == "__main__":
    unittest.main()
