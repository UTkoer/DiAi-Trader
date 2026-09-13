from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from predict_advanced.client import call_model, ModelFailure
from predict_advanced.data import validate_rows, prediction_cutoff, parse_timestamp, features
from predict_advanced.evaluate_results import summarize, evaluate
from predict_advanced.news import filter_news
from predict_advanced.runner import run, directory_name, advanced_name, publish_new
from predict_advanced.schema import EXAMPLE, validate_prediction, extract_json


def market_rows():
    return [{"trade_date": (datetime(2026, 7, 1) + timedelta(days=position)).strftime("%Y%m%d"),
             "open": 100 + position, "high": 102 + position, "low": 99 + position,
             "close": 101 + position, "vol": 1000, "amount": 10000, "pct_chg": .5}
            for position in range(35)]


def news_item(**updates):
    item = {"title": "Policy update", "url": "https://example.com/news", "source": "test",
            "published_at": "2026-07-30T10:00:00+08:00", "fetched_at": "2026-07-30T11:00:00+08:00",
            "content": "Historical snapshot", "index_codes": ["TEST"]}
    return {**item, **updates}


class DataTests(unittest.TestCase):
    def test_cutoff_and_future_invariance(self):
        rows = market_rows()
        history, quality = validate_rows(rows, "20260801", 30)
        self.assertEqual(history[-1]["trade_date"], "20260731")
        before = features(history)
        rows[-1]["close"] = 999999
        self.assertEqual(before, features(validate_rows(list(reversed(rows)), "20260801", 30)[0]))
        self.assertEqual(prediction_cutoff("20260801"), parse_timestamp("2026-07-31T16:00:00Z"))
        with self.assertRaises(ValueError):
            prediction_cutoff("20260801", "2026-08-01T15:00:00+08:00")

    def test_early_cutoff_and_invalid_data(self):
        rows = market_rows()
        cutoff = prediction_cutoff("20260801", "2026-07-30T00:00:00+08:00")
        self.assertEqual(validate_rows(rows, "20260801", 20, cutoff)[0][-1]["trade_date"], "20260729")
        for field, value in (("open", 999), ("vol", -1), ("close", float("nan")), ("low", 0), ("amount", True)):
            broken = copy.deepcopy(rows)
            broken[29][field] = value
            self.assertEqual(validate_rows(broken, "20260801", 30)[1]["status"], "error")
        self.assertIn("duplicate_trade_date", validate_rows(rows + [rows[0]], "20260801", 30)[1]["errors"])
        self.assertIn("invalid_trade_date", validate_rows(rows + [{}], "20260801", 30)[1]["errors"])

    def test_calendar_missing(self):
        rows = market_rows()
        calendar = [item["trade_date"] for item in rows]
        del rows[20]
        self.assertIn("missing_trading_sessions", validate_rows(rows, "20260801", 20, calendar=calendar)[1]["errors"])

    def test_news(self):
        cutoff = prediction_cutoff("20260801")
        items = [news_item(), news_item(), news_item(published_at=None),
                 news_item(published_at="2026-08-02T00:00:00Z"), news_item(fetched_at="2026-08-02T00:00:00Z"),
                 news_item(published_at="2026-07-30T10:00:00")]
        accepted, audit = filter_news(items, cutoff, index_code="TEST")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(audit), 6)
        self.assertIn("snapshot_after_cutoff", [item["reason"] for item in audit])
        self.assertEqual(filter_news([news_item()], cutoff, index_code="OTHER")[0], [])
        self.assertEqual(filter_news([], cutoff)[0], [])


class SchemaTests(unittest.TestCase):
    def test_valid_and_wrapped(self):
        text = "Here is JSON:\n```json\n" + json.dumps(EXAMPLE) + "\n```"
        self.assertEqual(validate_prediction(extract_json(text))["confidence"], 55)

    def test_invalid(self):
        for text in ('{"direction": NaN}', '{"key":1,"key":2}', '{} {}', 'not JSON'):
            with self.assertRaises(ValueError):
                extract_json(text)
        for key, value in (("probability_up", 1.2), ("probability_down", .9), ("direction", "down"),
                           ("candle_class", "strong_down"), ("expected_pct_change", float("inf")),
                           ("probability_up", True), ("risks", "risk"), ("prediction_interval", {"lower": 1, "upper": 2})):
            broken = {**EXAMPLE, key: value}
            with self.assertRaises(ValueError):
                validate_prediction(broken)
        with self.assertRaises(ValueError):
            validate_prediction({})


class FakeResponse:
    status = 200
    headers = {"x-request-id": "test-request"}

    def __init__(self, content=None):
        self.content = content or json.dumps(EXAMPLE)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps({"choices": [{"message": {"content": self.content}}], "usage": {"total_tokens": 10}}).encode()


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.model = {"name": "demo<advanced>", "basemodel": "demo", "api_key_env": "TEST_ADVANCED_KEY", "openai_base_url": "https://example.com/v1"}

    @patch.dict(os.environ, {"TEST_ADVANCED_KEY": "SECRET_DO_NOT_LOG"})
    def test_429_and_400(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "SECRET_DO_NOT_LOG", {}, io.BytesIO())
            return FakeResponse()
        result, attempts = call_model(self.model, "prompt", opener=opener, sleep=lambda delay: None)
        self.assertEqual(len(attempts), 2)
        self.assertNotIn("SECRET_DO_NOT_LOG", json.dumps(attempts))
        def bad(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, "SECRET_DO_NOT_LOG", {}, io.BytesIO())
        with self.assertRaises(ModelFailure) as caught:
            call_model(self.model, "prompt", opener=bad, sleep=lambda delay: None)
        self.assertEqual(len(caught.exception.attempts), 1)

    @patch.dict(os.environ, {"TEST_ADVANCED_KEY": "test"})
    def test_parse_retry(self):
        with self.assertRaises(ModelFailure) as caught:
            call_model(self.model, "prompt", max_retries=1, opener=lambda *args, **kwargs: FakeResponse("bad"), sleep=lambda delay: None)
        self.assertEqual(caught.exception.category, "parse_error")
        self.assertEqual(len(caught.exception.attempts), 2)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "indices"
        self.data.mkdir()
        (self.data / "TEST.json").write_text(json.dumps({"ts_code": "TEST", "name": "Test index", "records": market_rows()}), encoding="utf-8")
        self.config = {"prediction_date": "20260801", "lookback_days": 30, "data_dir": str(self.data),
                       "output_dir": str(self.root / "output"), "models": [
                           {"name": "demo<advanced>", "basemodel": "demo", "enabled": True,
                            "api_key_env": "TEST_ADVANCED_KEY", "openai_base_url": "https://example.com/v1"}]}

    def test_names(self):
        self.assertEqual(advanced_name("demo"), "demo<advanced>")
        self.assertEqual(directory_name("demo<advanced>"), "demo_advanced")
        self.assertNotIn("/", directory_name("../../demo"))

    def test_dry_run_writes_nothing(self):
        output = self.root / "output"
        report = run(self.config, dry_run=True, caller=lambda *args, **kwargs: self.fail("network call"))
        self.assertFalse(output.exists())
        entry = report["previews"][0]["indices"][0]
        self.assertNotIn('"actual"', entry["prompt"])
        self.assertNotIn('"trade_date": "20260801"', entry["prompt"])
        self.assertIn("TEST", entry["prompt"])
        self.assertEqual(report["written"], [])

    @patch.dict(os.environ, {"TEST_ADVANCED_KEY": "test"})
    def test_range_independence_compatibility_and_no_overwrite(self):
        config = copy.deepcopy(self.config)
        config.update(prediction_date="", prediction_start_date="20260801", prediction_end_date="20260802")
        config["models"].append({**config["models"][0], "name": "other", "basemodel": "other"})
        prompts = []
        def fake(model, prompt, **kwargs):
            prompts.append((model["name"], prompt))
            return validate_prediction(copy.deepcopy(EXAMPLE)), []
        with redirect_stdout(io.StringIO()):
            report = run(config, caller=fake)
        self.assertEqual(len(report["written"]), 4)
        self.assertEqual(prompts[0][1], prompts[1][1])
        payload = json.loads(Path(report["written"][0]).read_text(encoding="utf-8"))
        self.assertEqual(payload["agent_name"], "demo<advanced>")
        entry = payload["predictions"][0]
        for key in ("ts_code", "name", "as_of_date", "lookback", "news_context", "prediction", "actual", "verification"):
            self.assertIn(key, entry)
        for key in ("direction", "candle_class", "confidence", "expected_pct_change", "rationale", "market_analysis"):
            self.assertIn(key, entry["prediction"])
        self.assertIn("candle_correct", entry["verification"])
        original = Path(report["written"][0]).read_bytes()
        with self.assertRaises(FileExistsError):
            run(config, caller=lambda *args, **kwargs: self.fail("should not call API"))
        self.assertEqual(original, Path(report["written"][0]).read_bytes())
        self.assertEqual(evaluate(self.root / "output")["overall"]["valid"], 4)

    @patch.dict(os.environ, {"TEST_ADVANCED_KEY": "test"})
    def test_bad_index_does_not_stop_good(self):
        (self.data / "BAD.json").write_text("not-json", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            report = run(self.config, caller=lambda *args, **kwargs: (validate_prediction(copy.deepcopy(EXAMPLE)), []))
        entries = json.loads(Path(report["written"][0]).read_text(encoding="utf-8"))["predictions"]
        self.assertEqual(sum("prediction" in item for item in entries), 1)
        self.assertEqual(sum("error" in item for item in entries), 1)

    def test_new_file_only(self):
        path = self.root / "result.json"
        publish_new(path, {"old": 1})
        with self.assertRaises(FileExistsError):
            publish_new(path, {"new": 2})
        self.assertEqual(json.loads(path.read_text()), {"old": 1})

    def test_config_collision(self):
        self.config["models"].append({**self.config["models"][0], "name": "demo"})
        with self.assertRaises(ValueError):
            run(self.config, dry_run=True)


class EvaluationTests(unittest.TestCase):
    def test_denominators(self):
        prediction = validate_prediction(copy.deepcopy(EXAMPLE))
        result = summarize([{"prediction": prediction, "actual": {"direction": "up", "candle_class": "up", "pct_chg": .2}},
                            {"error": "failed", "error_type": "api_error"}, {"prediction": prediction, "actual": None}])
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["api_failures"], 1)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["direction_accuracy"], 1)
        self.assertAlmostEqual(result["brier_score"], .45 ** 2)
        self.assertIsNone(summarize([])["log_loss"])


if __name__ == "__main__":
    unittest.main()

class IntegrationEdgeTests(unittest.TestCase):
    setUp = RunnerTests.setUp

    @patch.dict(os.environ, {"TEST_ADVANCED_KEY": "test"})
    def test_api_failure_keeps_next_model_running(self):
        self.config["models"].append({**self.config["models"][0], "name": "second", "basemodel": "second"})
        def fake(model, prompt, **kwargs):
            if model["basemodel"] == "demo":
                raise ModelFailure("api_error", [{"error_type": "api_error"}])
            return validate_prediction(copy.deepcopy(EXAMPLE)), []
        with redirect_stdout(io.StringIO()):
            report = run(self.config, caller=fake)
        results = [json.loads(Path(path).read_text(encoding="utf-8"))["predictions"][0] for path in report["written"]]
        self.assertEqual(results[0]["error_type"], "api_error")
        self.assertIn("prediction", results[1])

    def test_news_in_prompt_and_future_news_removed(self):
        cache = self.root / "news.json"
        cache.write_text(json.dumps([news_item(content="ACCEPTED_SNAPSHOT"),
                                    news_item(title="future", url="https://example.com/future", content="FUTURE_SECRET",
                                              published_at="2026-08-03T00:00:00Z", fetched_at="2026-08-03T01:00:00Z")]), encoding="utf-8")
        self.config["news_cache"] = str(cache)
        report = run(self.config, dry_run=True)
        entry = report["previews"][0]["indices"][0]
        self.assertIn("ACCEPTED_SNAPSHOT", entry["prompt"])
        self.assertNotIn("FUTURE_SECRET", entry["prompt"])
        self.assertEqual(entry["news_status"], "ok")
        self.assertEqual(len(entry["news_audit"]), 2)


if __name__ == "__main__":
    unittest.main()

