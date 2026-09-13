from __future__ import annotations

import math
import statistics
from datetime import datetime, time, timezone, timedelta

MARKET_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
FIELDS = ("open", "high", "low", "close", "vol", "amount")


def date_key(value):
    text = str(value)
    pattern = "%Y-%m-%d" if "-" in text else "%Y%m%d"
    parsed = datetime.strptime(text, pattern)
    if parsed.strftime(pattern) != text:
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return parsed.strftime("%Y%m%d")


def parse_timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("ISO timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp timezone is required")
    return parsed.astimezone(timezone.utc)


def prediction_cutoff(prediction_date, explicit=None):
    target = datetime.strptime(date_key(prediction_date), "%Y%m%d")
    start = datetime.combine(target.date(), time.min, MARKET_TZ).astimezone(timezone.utc)
    cutoff = parse_timestamp(explicit) if explicit else start
    if cutoff > start:
        raise ValueError("cutoff cannot be after target-day start")
    return cutoff


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("expected finite numeric value")
    return float(value)


def candle_class(change):
    return "strong_down" if change <= -1 else "down" if change < 0 else "up" if change < 1 else "strong_up"


def validate_rows(records, prediction_date, lookback=30, cutoff=None, calendar=None):
    if type(lookback) is not int or lookback < 2:
        raise ValueError("lookback must be at least 2")
    target = date_key(prediction_date)
    cutoff = cutoff or prediction_cutoff(target)
    errors, warnings, candidates, rows = [], [], [], []
    for row in records:
        try:
            day = date_key(row["trade_date"])
        except (KeyError, TypeError, ValueError):
            errors.append("invalid_trade_date")
            continue
        close_time = datetime.combine(datetime.strptime(day, "%Y%m%d").date(), time(15), MARKET_TZ)
        if day < target and close_time <= cutoff:
            candidates.append((day, row))
    dates = [day for day, row in candidates]
    if dates != sorted(dates):
        warnings.append("input_sorted_by_trade_date")
    if len(dates) != len(set(dates)):
        errors.append("duplicate_trade_date")
    candidates.sort(key=lambda item: item[0])
    for day, row in candidates[-lookback:]:
        try:
            values = {key: number(row[key]) for key in FIELDS}
            if any(values[key] <= 0 for key in ("open", "high", "low", "close")):
                raise ValueError("nonpositive_price")
            if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                raise ValueError("invalid_ohlc")
            if values["vol"] < 0 or values["amount"] < 0:
                raise ValueError("negative_volume_or_amount")
            values["pct_chg"] = number(row["pct_chg"]) if "pct_chg" in row else None
            rows.append({"trade_date": day, **values})
        except (KeyError, ValueError, TypeError):
            errors.append(f"invalid_ohlcv:{day}")
    if len(rows) < lookback:
        errors.append("insufficient_history")
    if calendar is None:
        warnings.append("no_exchange_calendar_missing_sessions_unverified")
    else:
        eligible = sorted(day for day in calendar if day < target and datetime.combine(
            datetime.strptime(day, "%Y%m%d").date(), time(15), MARKET_TZ) <= cutoff)
        if set(eligible[-lookback:]) - set(dates):
            errors.append("missing_trading_sessions")
        if set(dates) - set(calendar):
            errors.append("unexpected_trading_sessions")
    return rows, {"status": "error" if errors else "warning" if warnings else "ok",
                  "errors": sorted(set(errors)), "warnings": warnings, "count": len(rows),
                  "coverage_start": rows[0]["trade_date"] if rows else None,
                  "coverage_end": rows[-1]["trade_date"] if rows else None}


def features(rows):
    if not rows:
        return {}
    closes = [row["close"] for row in rows]
    volumes = [row["vol"] for row in rows]
    returns = [current / previous - 1 for previous, current in zip(closes, closes[1:])]
    result = {"history_count": len(rows), "units": "returns and volatility: percent"}
    for window in (1, 3, 5, 10, 20):
        result[f"return_{window}d_pct"] = 100 * (closes[-1] / closes[-window - 1] - 1) if len(closes) > window else None
    for window in (5, 10, 20):
        average = statistics.mean(closes[-window:]) if len(closes) >= window else None
        result[f"ma_{window}"] = average
        result[f"ma_deviation_{window}_pct"] = 100 * (closes[-1] / average - 1) if average else None
        result[f"volatility_{window}d_pct"] = 100 * statistics.pstdev(returns[-window:]) if len(returns) >= window else None
    last = rows[-1]
    spread = last["high"] - last["low"]
    result.update({
        "amplitude_pct": 100 * spread / closes[-2] if len(closes) > 1 else None,
        "body_ratio": abs(last["close"] - last["open"]) / spread if spread else 0,
        "upper_shadow_ratio": (last["high"] - max(last["close"], last["open"])) / spread if spread else 0,
        "lower_shadow_ratio": (min(last["close"], last["open"]) - last["low"]) / spread if spread else 0,
        "volume_change_pct": 100 * (volumes[-1] / volumes[-2] - 1) if len(volumes) > 1 and volumes[-2] else None,
        "volume_relative_20": volumes[-1] / statistics.mean(volumes[-20:]) if len(volumes) >= 20 and sum(volumes[-20:]) else None,
        "distance_high_20_pct": 100 * (closes[-1] / max(row["high"] for row in rows[-20:]) - 1),
        "distance_low_20_pct": 100 * (closes[-1] / min(row["low"] for row in rows[-20:]) - 1),
    })
    for label, sign in (("consecutive_up", 1), ("consecutive_down", -1)):
        count = 0
        for change in reversed(returns):
            if change * sign <= 0:
                break
            count += 1
        result[label] = count
    deviation = result["ma_deviation_20_pct"]
    volatility = result["volatility_20d_pct"]
    result["trend"] = "unknown" if deviation is None else "up" if deviation > 1 else "down" if deviation < -1 else "sideways"
    result["volatility"] = "unknown" if volatility is None else "high" if volatility > 2 else "normal"
    result["volume_price"] = ("up_expanding" if returns[-1] > 0 else "down_expanding" if returns[-1] < 0 else "flat") if returns and volumes[-1] > volumes[-2] else "not_expanding"
    return result


def actual_outcome(records, prediction_date):
    matches = []
    for row in records:
        try:
            if date_key(row["trade_date"]) == date_key(prediction_date):
                matches.append(row)
        except (KeyError, TypeError, ValueError):
            continue
    if len(matches) != 1:
        return None
    try:
        change = number(matches[0]["pct_chg"])
        prices = {key: number(matches[0][key]) for key in ("open", "high", "low", "close")}
    except (KeyError, ValueError):
        return None
    return {"trade_date": date_key(prediction_date), "pct_chg": change,
            "direction": "up" if change >= 0 else "down", "candle_class": candle_class(change), **prices}
