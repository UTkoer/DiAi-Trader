from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

CLASSES = ("strong_down", "down", "up", "strong_up")


def summarize(items):
    result = {"total": len(items), "valid": 0, "pending": 0, "invalid_samples": 0,
              "api_failures": 0, "parse_failures": 0, "data_failures": 0, "other_failures": 0,
              "news_unavailable": sum(item.get("news_status") != "ok" for item in items)}
    valid = []
    for item in items:
        if item.get("error"):
            category = item.get("error_type")
            field = "api_failures" if category in ("api_error", "network_error", "configuration_error") else "parse_failures" if category == "parse_error" else "data_failures" if category == "data_error" else "other_failures"
            result[field] += 1
            continue
        prediction, actual = item.get("prediction"), item.get("actual")
        if not prediction or not actual:
            result["pending"] += 1
            continue
        try:
            probability = prediction["probability_up"]
            expected, change = prediction["expected_pct_change"], actual["pct_chg"]
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in (probability, expected, change)):
                raise ValueError("nonfinite")
            if not 0 <= probability <= 1 or prediction["direction"] not in ("up", "down") or actual["direction"] not in ("up", "down"):
                raise ValueError("invalid_direction_probability")
            if prediction["candle_class"] not in CLASSES or actual["candle_class"] not in CLASSES:
                raise ValueError("invalid_class")
            valid.append((prediction, actual))
        except (KeyError, TypeError, ValueError):
            result["invalid_samples"] += 1
    count = len(valid)
    result["valid"] = count
    confusion = {label: {predicted: 0 for predicted in CLASSES} for label in CLASSES}
    binary = {label: {predicted: 0 for predicted in ("up", "down")} for label in ("up", "down")}
    bins = [{"count": 0, "probability_sum": 0, "up_count": 0} for position in range(10)]
    confidence = defaultdict(lambda: {"count": 0, "correct": 0})
    brier, loss = 0, 0
    for prediction, actual in valid:
        probability = prediction["probability_up"]
        label = int(actual["direction"] == "up")
        brier += (probability - label) ** 2
        clipped = max(1e-15, min(1 - 1e-15, probability))
        loss -= label * math.log(clipped) + (1 - label) * math.log(1 - clipped)
        confusion[actual["candle_class"]][prediction["candle_class"]] += 1
        binary[actual["direction"]][prediction["direction"]] += 1
        bucket = bins[min(9, int(probability * 10))]
        bucket["count"] += 1
        bucket["probability_sum"] += probability
        bucket["up_count"] += label
        bucket = confidence[prediction.get("confidence_level", "unknown")]
        bucket["count"] += 1
        bucket["correct"] += prediction["direction"] == actual["direction"]
    result.update(direction_accuracy=sum(prediction["direction"] == actual["direction"] for prediction, actual in valid) / count if count else None,
                  candle_accuracy=sum(prediction["candle_class"] == actual["candle_class"] for prediction, actual in valid) / count if count else None,
                  brier_score=brier / count if count else None, log_loss=loss / count if count else None,
                  mean_expected_pct_change=sum(prediction["expected_pct_change"] for prediction, actual in valid) / count if count else None,
                  mean_actual_pct_change=sum(actual["pct_chg"] for prediction, actual in valid) / count if count else None,
                  direction_signed_actual_pct=sum(actual["pct_chg"] * (1 if prediction["direction"] == "up" else -1) for prediction, actual in valid) / count if count else None,
                  confusion_matrix=confusion, direction_confusion_matrix=binary)
    result["per_class"] = {}
    for label in CLASSES:
        support, predicted = sum(confusion[label].values()), sum(row[label] for row in confusion.values())
        result["per_class"][label] = {"support": support, "precision": confusion[label][label] / predicted if predicted else None,
                                    "recall": confusion[label][label] / support if support else None}
    result["calibration"] = [{"lower": position / 10, "upper": (position + 1) / 10, "count": bucket["count"],
                               "mean_probability": bucket["probability_sum"] / bucket["count"] if bucket["count"] else None,
                               "actual_up_rate": bucket["up_count"] / bucket["count"] if bucket["count"] else None} for position, bucket in enumerate(bins)]
    result["confidence_accuracy"] = {key: {**value, "accuracy": value["correct"] / value["count"]} for key, value in confidence.items()}
    return result


def evaluate(path):
    records, unreadable = [], 0
    for file in sorted(Path(path).glob("*_advanced/*.json")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8-sig"))
            if payload.get("schema_version") != "advanced-1.0":
                continue
            for item in payload["predictions"]:
                records.append({**item, "model": payload["agent_name"], "date": payload["prediction_date"]})
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            unreadable += 1
    records.sort(key=lambda item: (item["date"], item["model"], item.get("ts_code", "")))
    result = {"overall": summarize(records), "unreadable_files": unreadable}
    for field in ("model", "ts_code", "date"):
        groups = defaultdict(list)
        for item in records:
            groups[item.get(field, "unknown")].append(item)
        result["by_" + field] = {key: summarize(items) for key, items in groups.items()}
    regimes = defaultdict(list)
    comparisons = defaultdict(list)
    for item in records:
        regimes[item.get("features", {}).get("trend", "unknown")].append(item)
        if item.get("prediction"):
            comparisons[(item["date"], item.get("ts_code"))].append(item["prediction"]["direction"])
    result["by_regime"] = {key: summarize(items) for key, items in regimes.items()}
    pairs = [values for values in comparisons.values() if len(values) > 1]
    result["disagreement"] = {"comparable_samples": len(pairs), "disagreeing_samples": sum(len(set(values)) > 1 for values in pairs),
                              "policy": "post-hoc evaluation only, never used in predictions"}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate saved advanced results without API calls")
    parser.add_argument("path", nargs="?", default=str(Path(__file__).resolve().parent.parent / "data" / "predict"))
    print(json.dumps(evaluate(parser.parse_args().path), ensure_ascii=False, indent=2, allow_nan=False))
