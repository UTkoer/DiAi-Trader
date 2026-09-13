from __future__ import annotations

import json
import re
from .data import number, candle_class

EXAMPLE = {
    "direction": "up", "candle_class": "up", "probability_up": 0.55,
    "probability_down": 0.45, "expected_pct_change": 0.2,
    "prediction_interval": {"lower": -1.0, "upper": 1.0}, "confidence_level": "low",
    "rationale": "Evidence-based explanation", "market_analysis": "Market implications",
    "evidence": {"positive": [], "negative": [], "neutral": []},
    "risks": ["Uncertainty"], "counter_view": "Why this prediction could fail",
}


def reject_constant(value):
    raise ValueError("nonfinite_json_number")


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def extract_json(text):
    if not isinstance(text, str) or len(text) > 200000:
        raise ValueError("invalid_response_text")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    decoder = json.JSONDecoder(parse_constant=reject_constant, object_pairs_hook=unique_pairs)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("missing_json_object")
    value, end = decoder.raw_decode(cleaned[start:])
    if "{" in cleaned[start + end:] or "}" in cleaned[start + end:]:
        raise ValueError("ambiguous_multiple_objects")
    if not isinstance(value, dict):
        raise ValueError("expected_json_object")
    return value


def validate_prediction(value):
    if not isinstance(value, dict) or any(key not in value for key in EXAMPLE):
        raise ValueError("missing_prediction_fields")
    up, down, change = (number(value[key]) for key in ("probability_up", "probability_down", "expected_pct_change"))
    if not 0 <= up <= 1 or not 0 <= down <= 1 or abs(up + down - 1) > 1e-6:
        raise ValueError("invalid_probabilities")
    if value["direction"] not in ("up", "down") or value["direction"] != ("up" if up >= down else "down"):
        raise ValueError("direction_probability_conflict")
    if value["candle_class"] != candle_class(change) or value["direction"] != ("up" if change >= 0 else "down"):
        raise ValueError("direction_candle_return_conflict")
    interval = value["prediction_interval"]
    if not isinstance(interval, dict) or set(interval) != {"lower", "upper"}:
        raise ValueError("invalid_interval")
    if not number(interval["lower"]) <= change <= number(interval["upper"]):
        raise ValueError("interval_excludes_expected_change")
    if value["confidence_level"] not in ("low", "medium", "high"):
        raise ValueError("invalid_confidence_level")
    for key in ("rationale", "market_analysis", "counter_view"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 12000:
            raise ValueError("invalid_explanation")
    evidence = value["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"positive", "negative", "neutral"}:
        raise ValueError("invalid_evidence")
    for entries in [value["risks"], *evidence.values()]:
        if not isinstance(entries, list) or len(entries) > 30 or any(not isinstance(item, str) or len(item) > 4000 for item in entries):
            raise ValueError("invalid_evidence_or_risks")
    result = {key: value[key] for key in EXAMPLE}
    result["confidence"] = round(100 * max(up, down), 4)
    return result
