"""Point-in-time daily OHLCV features; no I/O, model fitting or forecasting."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, time
from typing import Any

from .data import MARKET_TZ, date_key, number, prediction_cutoff

WINDOWS = (5, 10, 20, 60)
PRICE_FIELDS = ("open", "high", "low", "close")


def feature_definitions() -> dict[str, Any]:
    return {
        "version": "1.0",
        "frequency": "daily; A-share session closes at 15:00 Asia/Shanghai",
        "required_fields": ["trade_date", "open", "high", "low", "close", "vol"],
        "optional_fields": ["amount", "ts_code"],
        "ignored_fields": ["pct_chg", "pre_close", "change"],
        "units": {"*_pct": "percentage points; 1 means 1%", "prices": "input price unit",
                  "volume": "input vol unit; no conversion", "amount": "input amount unit; no conversion",
                  "ratios": "dimensionless", "volatility": "population stddev of daily simple returns; not annualized"},
        "formulas": {
            "return_Nd_pct": "100 * (close[-1] / close[-N-1] - 1); requires N+1 closes",
            "sma_N": "mean of last N closes; requires N closes",
            "sma_deviation_N_pct": "100 * (last close / sma_N - 1)",
            "volatility_Nd_pct": "100 * population stddev of last N daily returns; requires N+1 closes",
            "max_drawdown_N_pct": "100 * minimum(close / running peak - 1) within last N closes; <= 0",
            "volume_relative_previous_20": "latest volume / mean of previous 20 volumes; requires 21 rows",
            "rsi_14_simple": "100 * mean(gains) / (mean(gains)+mean(losses)) over 14 changes; simple, not Wilder; flat=50",
            "atr_14_simple": "mean of last 14 true ranges max(high-low, abs(high-prev_close), abs(low-prev_close)); not Wilder",
            "bollinger_20": "SMA20 +/- 2 * population stddev of 20 closes; percent_b=(close-lower)/(upper-lower)",
            "gap_pct": "100 * (latest open / previous close - 1)",
            "amplitude_pct": "100 * (latest high - latest low) / previous close",
            "price_volume_correlation_20": "Pearson correlation between 20 daily price returns and volume percentage changes; undefined for constant or zero-prior-volume series",
            "distance_previous_high_20_pct": "100 * (latest close / max(previous 20 highs) - 1); excludes latest row",
            "distance_previous_low_20_pct": "100 * (latest close / min(previous 20 lows) - 1); excludes latest row",
        },
        "policies": ["No extra warmup outside the requested range", "No imputation or silent row deletion",
                     "Insufficient windows and zero denominators return null with a reason",
                     "No implied probability, buy/sell decision, volume unit conversion or calibrated signal"],
    }


def _maximum_drawdown(closes):
    peak, drawdown = closes[0], 0.0
    for close in closes:
        peak = max(peak, close)
        drawdown = min(drawdown, close / peak - 1)
    return 100 * drawdown


def _calculate(rows):
    closes = [row["close"] for row in rows]
    volumes = [row["vol"] for row in rows]
    returns = [current / previous - 1 for previous, current in zip(closes, closes[1:])]
    values, missing = {}, {}

    def store(key, required, calculation):
        if len(rows) < required:
            values[key] = None
            missing[key] = f"requires_{required}_sessions"
            return
        try:
            value = calculation()
            if value is None:
                values[key] = None
                missing[key] = "undefined_zero_denominator_or_missing_optional_data"
            elif isinstance(value, (int, float)) and not math.isfinite(value):
                values[key] = None
                missing[key] = "numeric_overflow"
            else:
                values[key] = value
        except (OverflowError, ZeroDivisionError, ValueError):
            values[key] = None
            missing[key] = "numeric_overflow_or_undefined"

    for window in (1, 3, 5, 10, 20, 60):
        store(f"return_{window}d_pct", window + 1, lambda: 100 * (closes[-1] / closes[-window - 1] - 1))
    for window in WINDOWS:
        store(f"sma_{window}", window, lambda: statistics.mean(closes[-window:]))
        store(f"sma_deviation_{window}_pct", window, lambda: 100 * (closes[-1] / statistics.mean(closes[-window:]) - 1))
        store(f"volatility_{window}d_pct", window + 1, lambda: 100 * statistics.pstdev(returns[-window:]))
        store(f"max_drawdown_{window}_pct", window, lambda: _maximum_drawdown(closes[-window:]))
    last = rows[-1]
    spread = last["high"] - last["low"]
    store("amplitude_pct", 2, lambda: 100 * spread / closes[-2])
    store("gap_pct", 2, lambda: 100 * (last["open"] / closes[-2] - 1))
    store("body_ratio", 1, lambda: abs(last["close"] - last["open"]) / spread if spread else 0.0)
    store("upper_shadow_ratio", 1, lambda: (last["high"] - max(last["open"], last["close"])) / spread if spread else 0.0)
    store("lower_shadow_ratio", 1, lambda: (min(last["open"], last["close"]) - last["low"]) / spread if spread else 0.0)
    store("volume_change_pct", 2, lambda: 100 * (volumes[-1] / volumes[-2] - 1) if volumes[-2] else None)
    store("volume_relative_previous_20", 21, lambda: volumes[-1] / statistics.mean(volumes[-21:-1]) if sum(volumes[-21:-1]) else None)
    store("mean_amount_20", 20, lambda: statistics.mean(row["amount"] for row in rows[-20:]) if all(row["amount"] is not None for row in rows[-20:]) else None)
    store("distance_previous_high_20_pct", 21, lambda: 100 * (closes[-1] / max(row["high"] for row in rows[-21:-1]) - 1))
    store("distance_previous_low_20_pct", 21, lambda: 100 * (closes[-1] / min(row["low"] for row in rows[-21:-1]) - 1))

    def rsi():
        changes = [current - previous for previous, current in zip(closes[-15:-1], closes[-14:])]
        gains = sum(max(change, 0) for change in changes)
        losses = sum(max(-change, 0) for change in changes)
        return 100 * gains / (gains + losses) if gains + losses else 50.0

    store("rsi_14_simple", 15, rsi)
    store("atr_14_simple", 15, lambda: statistics.mean(max(row["high"] - row["low"], abs(row["high"] - previous), abs(row["low"] - previous))
                                                       for previous, row in zip(closes[-15:-1], rows[-14:])))
    store("atr_14_pct", 15, lambda: 100 * values["atr_14_simple"] / closes[-1] if values["atr_14_simple"] is not None else None)
    store("bollinger_20_middle", 20, lambda: statistics.mean(closes[-20:]))
    store("bollinger_20_upper", 20, lambda: statistics.mean(closes[-20:]) + 2 * statistics.pstdev(closes[-20:]))
    store("bollinger_20_lower", 20, lambda: statistics.mean(closes[-20:]) - 2 * statistics.pstdev(closes[-20:]))
    store("bollinger_20_width_pct", 20, lambda: 400 * statistics.pstdev(closes[-20:]) / statistics.mean(closes[-20:]))
    store("bollinger_20_percent_b", 20, lambda: (closes[-1] - values["bollinger_20_lower"]) / (values["bollinger_20_upper"] - values["bollinger_20_lower"])
          if values["bollinger_20_upper"] is not None and values["bollinger_20_lower"] is not None and values["bollinger_20_upper"] != values["bollinger_20_lower"] else None)

    def correlation():
        if any(volume == 0 for volume in volumes[-21:-1]):
            return None
        volume_returns = [current / previous - 1 for previous, current in zip(volumes[-21:-1], volumes[-20:])]
        price_returns = returns[-20:]
        price_mean, volume_mean = statistics.mean(price_returns), statistics.mean(volume_returns)
        numerator = sum((price - price_mean) * (volume - volume_mean) for price, volume in zip(price_returns, volume_returns))
        denominator = math.sqrt(sum((value - price_mean) ** 2 for value in price_returns) * sum((value - volume_mean) ** 2 for value in volume_returns))
        return max(-1, min(1, numerator / denominator)) if denominator else None

    store("price_volume_correlation_20", 21, correlation)
    for label, sign in (("consecutive_up", 1), ("consecutive_down", -1)):
        count = 0
        for change in reversed(returns):
            if change * sign <= 0:
                break
            count += 1
        values[label] = count
    observations = []
    for key, positive, negative in (("sma_deviation_20_pct", "close_above_sma20", "close_below_sma20"),
                                     ("distance_previous_high_20_pct", "close_above_previous_20_session_high", None),
                                     ("distance_previous_low_20_pct", None, "close_below_previous_20_session_low")):
        value = values.get(key)
        if value is not None and ((value > 0 and positive) or (value < 0 and negative)):
            observations.append({"observation": positive if value > 0 else negative, "feature": key, "value": value})
    return values, missing, observations


def analyze_stock_features(
    records: list[dict[str, Any]], prediction_date: str,
    prediction_cutoff_datetime: str | None = None, start_date: str | None = None,
    end_date: str | None = None, symbol: str | None = None,
    trading_dates: list[str] | None = None,
) -> dict[str, Any]:
    """Analyze one instrument's daily bars strictly before target date/cutoff.

    Pass the actual OHLCV records, not a filename. vol is required, amount optional.
    Date range is inclusive. Daily bars complete at 15:00 Asia/Shanghai. No I/O.
    """
    result = {"schema_version": "stock-features-1.0", "status": "error", "features": {},
              "data_quality": {"errors": [], "warnings": []}, "unavailable_features": {}, "observations": []}
    errors, warnings = result["data_quality"]["errors"], result["data_quality"]["warnings"]
    try:
        target = date_key(prediction_date)
        cutoff = prediction_cutoff(target, prediction_cutoff_datetime)
        start, end = date_key(start_date) if start_date else None, date_key(end_date) if end_date else None
        if start and end and start > end:
            raise ValueError("invalid_range")
        if not isinstance(records, list) or len(records) > 5000:
            raise ValueError("records_limit")
        if symbol is not None and (not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 100):
            raise ValueError("invalid_symbol")
        if trading_dates is not None:
            if not isinstance(trading_dates, list) or len(trading_dates) > 10000:
                raise ValueError("invalid_calendar")
            calendar = sorted(set(date_key(day) for day in trading_dates))
        else:
            calendar = None
    except (TypeError, ValueError):
        errors.append("invalid_arguments_dates_or_limits")
        return result
    result.update(symbol=symbol, prediction_date=target, prediction_cutoff_time=cutoff.isoformat(),
                  requested_range={"start_date": start, "end_date": end},
                  input_count=len(records), excluded={"outside_range": 0, "at_or_after_target": 0, "after_cutoff": 0})
    selected, symbols = [], set()
    for row in records:
        try:
            day = date_key(row["trade_date"])
        except (KeyError, TypeError, ValueError):
            errors.append("invalid_trade_date")
            continue
        if day >= target:
            result["excluded"]["at_or_after_target"] += 1
            continue
        if (start and day < start) or (end and day > end):
            result["excluded"]["outside_range"] += 1
            continue
        if datetime.combine(datetime.strptime(day, "%Y%m%d").date(), time(15), MARKET_TZ) > cutoff:
            result["excluded"]["after_cutoff"] += 1
            continue
        code = row.get("ts_code")
        if code is not None:
            if not isinstance(code, str) or not code.strip():
                errors.append("invalid_row_symbol")
            else:
                symbols.add(code)
        try:
            values = {key: number(row[key]) for key in (*PRICE_FIELDS, "vol")}
            if any(not 1e-12 <= values[key] <= 1e12 for key in PRICE_FIELDS):
                raise ValueError("invalid_price")
            if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                raise ValueError("invalid_ohlc")
            if not (values["vol"] == 0 or 1e-12 <= values["vol"] <= 1e25):
                raise ValueError("invalid_volume")
            values["amount"] = number(row["amount"]) if row.get("amount") is not None else None
            if values["amount"] is not None and not 0 <= values["amount"] <= 1e25:
                raise ValueError("invalid_amount")
            selected.append({"trade_date": day, **values})
        except (KeyError, ValueError, TypeError):
            errors.append("invalid_ohlcv:" + day)
    if len(symbols) > 1 or (symbol and symbols and symbols != {symbol}):
        errors.append("mixed_or_mismatched_symbols")
    if not symbol and len(symbols) == 1:
        result["symbol"] = next(iter(symbols))
    if not symbols:
        warnings.append("instrument_identity_not_verifiable_from_rows")
    dates = [row["trade_date"] for row in selected]
    if len(dates) != len(set(dates)):
        errors.append("duplicate_trade_date")
    if dates != sorted(dates):
        warnings.append("input_sorted_by_trade_date")
    selected.sort(key=lambda row: row["trade_date"])
    result["coverage"] = {"count": len(selected), "start_date": selected[0]["trade_date"] if selected else None,
                           "end_date": selected[-1]["trade_date"] if selected else None}
    if not selected:
        errors.append("no_eligible_sessions")
    if calendar is None:
        warnings.append("missing_trading_sessions_unverified_without_exchange_calendar")
    elif selected:
        lower, upper = start or selected[0]["trade_date"], end or selected[-1]["trade_date"]
        expected = {day for day in calendar if lower <= day <= upper and day < target and datetime.combine(
            datetime.strptime(day, "%Y%m%d").date(), time(15), MARKET_TZ) <= cutoff}
        missing = sorted(expected - set(dates))
        if missing:
            errors.append("missing_trading_sessions")
            result["data_quality"]["missing_dates"] = missing
        if set(dates) - set(calendar):
            errors.append("dates_not_in_exchange_calendar")
    if errors:
        result["data_quality"]["errors"] = sorted(set(errors))
        return result
    if any(row["amount"] is None for row in selected):
        warnings.append("optional_amount_missing")
    if any(row["vol"] == 0 for row in selected):
        warnings.append("zero_volume_sessions")
    values, missing, observations = _calculate(selected)
    if missing:
        warnings.append("some_features_unavailable_see_reasons")
    result.update(status="warning" if warnings else "ok", features=values,
                  unavailable_features=missing, observations=observations,
                  input_sha256=hashlib.sha256(json.dumps(selected, sort_keys=True, allow_nan=False).encode()).hexdigest(),
                  units=feature_definitions()["units"],
                  limitations=["Uses only the provided daily rows; no external warmup or imputation",
                               "Volume and amount retain input units; no VWAP derived from their ratio",
                               "Cannot verify adjustments, revisions or LLM historical memory contamination",
                               "Descriptive features, not trading advice or direction probabilities"])
    return result

