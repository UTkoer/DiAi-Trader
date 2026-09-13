"""Deterministic feature tests and an optional real MCP stdio integration test."""

from __future__ import annotations

import copy
import json
import math
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from predict_advanced.feature_analysis import analyze_stock_features, feature_definitions
from predict_advanced.mcp_client import MCPFeatureClient
from predict_advanced.mcp_tools.feature_analysis_server import handle


def bars(count=65, flat=False):
    rows = []
    for position in range(count):
        close = 100.0 if flat else 100.0 + position
        rows.append({"ts_code": "TEST", "trade_date": (datetime(2026, 1, 1) + timedelta(days=position)).strftime("%Y%m%d"),
                     "open": close, "high": close + 2, "low": close - 2, "close": close,
                     "vol": 1000.0, "amount": 100000.0})
    return rows


def analyze(rows, **kwargs):
    return analyze_stock_features(rows, prediction_date="20260401", **kwargs)


class FeatureAnalysisTests(unittest.TestCase):
    def test_known_values(self):
        report = analyze(bars())
        values = report["features"]
        self.assertAlmostEqual(values["return_1d_pct"], 100 * (164 / 163 - 1))
        self.assertAlmostEqual(values["return_20d_pct"], 100 * (164 / 144 - 1))
        self.assertAlmostEqual(values["sma_20"], (145 + 164) / 2)
        self.assertEqual(values["rsi_14_simple"], 100)
        self.assertEqual(values["atr_14_simple"], 4)
        self.assertEqual(values["max_drawdown_60_pct"], 0)
        self.assertEqual(values["volume_relative_previous_20"], 1)
        self.assertAlmostEqual(values["distance_previous_high_20_pct"], 100 * (164 / 165 - 1))
        self.assertEqual(values["consecutive_up"], 64)
        self.assertIsNone(values["price_volume_correlation_20"])
        json.dumps(report, allow_nan=False)

    def test_flat_series(self):
        values = analyze(bars(flat=True))["features"]
        self.assertEqual(values["rsi_14_simple"], 50)
        self.assertEqual(values["volatility_20d_pct"], 0)
        self.assertIsNone(values["bollinger_20_percent_b"])
        self.assertEqual(values["consecutive_up"], 0)

    def test_true_range_gap_and_drawdown(self):
        rows = bars(21, flat=True)
        rows[-1].update(open=80, high=82, low=78, close=80)
        values = analyze(rows)["features"]
        self.assertEqual(values["max_drawdown_20_pct"], -19.999999999999996)
        self.assertAlmostEqual(values["atr_14_simple"], (13 * 4 + 22) / 14)
        self.assertEqual(values["rsi_14_simple"], 0)

    def test_short_and_optional_amount(self):
        rows = bars(3)
        for row in rows:
            del row["amount"]
        report = analyze(rows)
        self.assertEqual(report["status"], "warning")
        self.assertIsNone(report["features"]["return_3d_pct"])
        self.assertEqual(report["unavailable_features"]["return_3d_pct"], "requires_4_sessions")
        self.assertIn("optional_amount_missing", report["data_quality"]["warnings"])
        self.assertIsNotNone(report["features"]["return_1d_pct"])

    def test_time_range_sort_and_no_mutation(self):
        rows = bars()
        original = copy.deepcopy(rows)
        report = analyze(list(reversed(rows)), start_date="20260201", end_date="20260205")
        self.assertEqual(report["coverage"]["count"], 5)
        self.assertIsNone(report["features"]["return_5d_pct"])
        self.assertEqual(rows, original)
        ordered = analyze(rows, start_date="20260201", end_date="20260205")
        self.assertEqual(report["input_sha256"], ordered["input_sha256"])
        self.assertEqual(report["features"], ordered["features"])

    def test_future_bars_and_early_cutoff(self):
        rows = bars()
        target = "20260201"
        report = analyze_stock_features(rows, target)
        self.assertEqual(report["coverage"]["end_date"], "20260131")
        rows[-1].update(close=999999, vol=-1, ts_code="OTHER")
        after = analyze_stock_features(rows, target)
        self.assertEqual(after["features"], report["features"])
        self.assertEqual(after["input_sha256"], report["input_sha256"])
        early = analyze_stock_features(rows, target, prediction_cutoff_datetime="2026-01-30T06:59:00Z")
        self.assertEqual(early["coverage"]["end_date"], "20260129")
        self.assertEqual(analyze_stock_features(rows, target, prediction_cutoff_datetime="2026-02-01T10:00:00+08:00")["status"], "error")

    def test_reject_bad_rows_and_symbols(self):
        for field, value in (("open", True), ("close", "100"), ("low", -1), ("high", 1),
                             ("vol", -1), ("amount", float("inf")), ("close", float("nan")), ("close", 1e-320)):
            rows = bars()
            rows[10][field] = value
            report = analyze(rows)
            self.assertEqual(report["status"], "error", field)
            self.assertEqual(report["features"], {})
        rows = bars()
        rows[-1]["ts_code"] = "OTHER"
        self.assertIn("mixed_or_mismatched_symbols", analyze(rows)["data_quality"]["errors"])
        self.assertEqual(analyze(bars(), symbol="OTHER")["status"], "error")
        self.assertEqual(analyze(bars() + [bars()[0]])["status"], "error")
        self.assertEqual(analyze([{}])["status"], "error")
        self.assertEqual(analyze([])["status"], "error")

    def test_calendar_and_zero_volume(self):
        rows = bars()
        calendar = [row["trade_date"] for row in rows]
        complete = analyze(rows, trading_dates=calendar)
        self.assertNotIn("missing_trading_sessions_unverified_without_exchange_calendar", complete["data_quality"]["warnings"])
        del rows[25]
        self.assertIn("missing_trading_sessions", analyze(rows, trading_dates=calendar)["data_quality"]["errors"])
        rows = bars()
        rows[-2]["vol"] = 0
        report = analyze(rows)
        self.assertIsNone(report["features"]["volume_change_pct"])
        self.assertIsNone(report["features"]["price_volume_correlation_20"])
        json.dumps(report, allow_nan=False)

    def test_argument_limits(self):
        self.assertEqual(analyze([{}] * 5001)["status"], "error")
        self.assertEqual(analyze(bars(), start_date="20260301", end_date="20260201")["status"], "error")
        self.assertEqual(analyze(bars(), start_date="2026-2-1")["status"], "error")
        self.assertEqual(analyze(bars(), trading_dates=["invalid"])["status"], "error")

    def test_protocol_handler(self):
        initialized = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "predict-stock-features")
        listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual({tool["name"] for tool in listed["result"]["tools"]}, {"analyze_stock_features", "feature_definitions"})
        called = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "analyze_stock_features", "arguments": {"records": bars(), "prediction_date": "20260401"}}})
        self.assertEqual(called["result"]["structuredContent"]["coverage"]["count"], 65)
        self.assertIn("formulas", feature_definitions())


class MCPStdioTests(unittest.TestCase):
    def test_list_and_call_tools(self):
        script = Path(__file__).resolve().parents[1] / "mcp_tools" / "feature_analysis_server.py"
        with MCPFeatureClient(sys.executable, [str(script)], timeout=10,
                              cwd=Path(__file__).resolve().parents[2]) as client:
            payload, audit = client.analyze({"records": bars(), "prediction_date": "20260401"})
        self.assertEqual(payload["coverage"]["count"], 65)
        self.assertEqual(payload["features"]["rsi_14_simple"], 100)
        self.assertEqual(audit["transport"], "stdio")


if __name__ == "__main__":
    unittest.main()
