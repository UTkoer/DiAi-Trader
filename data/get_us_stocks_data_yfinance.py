#!/usr/bin/env python3
"""Incrementally download U.S. daily prices with yfinance.

The script writes the same ``data/USstocks`` structure as the Tushare and
AkShare downloaders. It does not need an API token or a remote symbol-list
request; symbols come from CLI arguments or a local JSON file.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("US_STOCKS_DATA_DIR", ROOT / "USstocks"))
STOCKS_DIR = DATA_DIR / "stocks"
DAILY_DIR = DATA_DIR / "daily"
START_DEFAULT = "20250101"
YFINANCE_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "vol",
}


def normalize_date(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "").replace("/", "")[:8]


def yahoo_symbol(value: str) -> str:
    """Convert common class-share notation, e.g. BRK.B to BRK-B."""
    return str(value or "").strip().upper().replace(".", "-")


def json_clean(value):
    if isinstance(value, dict):
        return {key: json_clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if hasattr(value, "item"):
            scalar = value.item()
            if isinstance(scalar, float) and not math.isfinite(scalar):
                return None
            return scalar
    except (TypeError, ValueError):
        pass
    return value


def read_payload(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def load_symbols_file(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get("symbols")
        if values is None and len(payload) == 1:
            values = next(iter(payload.values()))
    else:
        values = None
    if not isinstance(values, list):
        raise ValueError("代码文件必须是 JSON 数组，或包含 symbols 数组的 JSON 对象")
    return [str(value).strip().upper() for value in values if str(value).strip()]


def resolve_symbols(symbols: list[str] | None, symbols_file: str | None) -> list[str]:
    values = list(symbols or [])
    if symbols_file:
        values.extend(load_symbols_file(Path(symbols_file)))
    if not values:
        default_file = DATA_DIR / "symbols" / "us_stock.json"
        if default_file.exists():
            values.extend(load_symbols_file(default_file))
        else:
            raise ValueError("请使用 --symbols 或 --symbols-file 提供美股代码")
    return sorted(set(str(value).strip().upper() for value in values if str(value).strip()))


def fetch_history(symbol: str, start: str, end: str, auto_adjust: bool, retries: int) -> list[dict]:
    start_date = datetime.strptime(start, "%Y%m%d").strftime("%Y-%m-%d")
    exclusive_end = (datetime.strptime(end, "%Y%m%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            frame = yf.Ticker(yahoo_symbol(symbol)).history(
                start=start_date,
                end=exclusive_end,
                interval="1d",
                auto_adjust=auto_adjust,
                actions=False,
                repair=True,
                raise_errors=True,
            )
            if frame is None or frame.empty:
                return []
            frame = frame.reset_index()
            date_column = "Date" if "Date" in frame else frame.columns[0]
            frame = frame.rename(columns={date_column: "trade_date", **YFINANCE_COLUMNS})
            required = {"trade_date", "open", "high", "low", "close", "vol"}
            missing = required - set(frame.columns)
            if missing:
                raise RuntimeError(f"yfinance 历史行情缺少字段：{', '.join(sorted(missing))}")
            frame["trade_date"] = frame["trade_date"].map(normalize_date)
            for column in ("open", "high", "low", "close", "adj_close", "vol"):
                if column in frame:
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
            columns = [column for column in ("trade_date", *YFINANCE_COLUMNS.values()) if column in frame]
            return frame[columns].sort_values("trade_date").to_dict("records")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(attempt * 2)
    raise RuntimeError(f"yfinance 历史行情请求失败：{last_error}")


def enrich_records(records: list[dict]) -> list[dict]:
    if not records:
        return []
    frame = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "vol"):
        if column not in frame:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    calculated_amount = frame["close"] * frame["vol"]
    if "amount" in frame:
        frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(calculated_amount)
    else:
        frame["amount"] = calculated_amount
    frame["pre_close"] = frame["close"].shift(1)
    frame["change"] = frame["close"] - frame["pre_close"]
    frame["pct_change"] = frame["change"] / frame["pre_close"] * 100
    frame["pct_chg"] = frame["pct_change"]
    returns = frame["close"].pct_change()
    frame["mom_5"] = frame["close"].pct_change(5)
    frame["mom_10"] = frame["close"].pct_change(10)
    frame["mom_20"] = frame["close"].pct_change(20)
    frame["volatility_10"] = returns.rolling(10).std()
    frame["volatility_20"] = returns.rolling(20).std()
    frame["ma5"] = frame["close"].rolling(5).mean()
    frame["ma10"] = frame["close"].rolling(10).mean()
    frame["ma20"] = frame["close"].rolling(20).mean()
    frame["ma20_dev"] = (frame["close"] - frame["ma20"]) / frame["ma20"]
    frame["vol_ratio"] = frame["vol"].rolling(5).mean() / frame["vol"].rolling(20).mean()
    frame["amount_ma5"] = frame["amount"].rolling(5).mean()
    if "vwap" not in frame:
        frame["vwap"] = None
    return frame.where(pd.notna(frame), None).to_dict("records")


def write_market_files(stocks: list[dict], start: str, end: str, auto_adjust: bool) -> None:
    rows_by_date: dict[str, list[dict]] = {}
    symbols = []
    name_map = {}
    for stock in stocks:
        code = stock["ts_code"]
        name = stock["name"]
        symbols.append(code)
        name_map[code] = name
        for row in stock["records"]:
            trade_date = normalize_date(row.get("trade_date"))
            if trade_date:
                rows_by_date.setdefault(trade_date, []).append({**row, "name": name})

    dates = sorted(rows_by_date)
    for trade_date in dates:
        rows = rows_by_date[trade_date]
        with (DAILY_DIR / f"{trade_date}.json").open("w", encoding="utf-8") as handle:
            json.dump(
                json_clean({"date": trade_date, "asset_count": len(rows), "data": rows}),
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )

    latest_rows = rows_by_date.get(dates[-1], []) if dates else []
    with (DATA_DIR / "latest.json").open("w", encoding="utf-8") as handle:
        json.dump(
            json_clean({"date": dates[-1] if dates else None, "asset_count": len(latest_rows), "data": latest_rows}),
            handle,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    with (DATA_DIR / "dates.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {"dates": dates, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            handle,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    with (DATA_DIR / "meta.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "asset_type": "US_STOCK",
                "source": "yfinance",
                "auto_adjust": auto_adjust,
                "stock_count": len(symbols),
                "symbols": symbols,
                "name_map": name_map,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "start_date": dates[0] if dates else start,
                "end_date": dates[-1] if dates else end,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    symbols_dir = DATA_DIR / "symbols"
    symbols_dir.mkdir(parents=True, exist_ok=True)
    with (symbols_dir / "us_stock.json").open("w", encoding="utf-8") as handle:
        json.dump({"symbolname": "美股", "symbols": symbols}, handle, ensure_ascii=False, indent=2)


def main() -> None:
    global DATA_DIR, STOCKS_DIR, DAILY_DIR

    parser = argparse.ArgumentParser(description="使用 yfinance 增量下载美股日线行情")
    parser.add_argument("--start", default=START_DEFAULT, help="开始日期，格式 YYYYMMDD")
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"), help="结束日期，格式 YYYYMMDD")
    parser.add_argument("--symbols", nargs="*", help="只下载指定代码，例如 AAPL MSFT NVDA")
    parser.add_argument("--symbols-file", help="从 JSON 文件读取代码列表")
    parser.add_argument("--auto-adjust", action="store_true", help="使用 yfinance 自动复权价格")
    parser.add_argument("--limit", type=int, default=0, help="仅调试时限制下载数量，0 表示不限制")
    parser.add_argument("--retries", type=int, default=3, help="单个标的请求失败时的尝试次数")
    parser.add_argument("--output", default=DATA_DIR, help="数据输出目录")
    args = parser.parse_args()

    DATA_DIR = Path(args.output)
    STOCKS_DIR = DATA_DIR / "stocks"
    DAILY_DIR = DATA_DIR / "daily"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STOCKS_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    symbols = resolve_symbols(args.symbols, args.symbols_file)
    if args.limit:
        symbols = symbols[: args.limit]
    start_arg = normalize_date(args.start)
    end = normalize_date(args.end)
    completed = []

    for index, code in enumerate(symbols, start=1):
        path = STOCKS_DIR / f"{code}.json"
        existing_payload = read_payload(path)
        existing = existing_payload.get("records") or []
        name = str(existing_payload.get("name") or code)
        latest = max((normalize_date(row.get("trade_date")) for row in existing), default="")
        fetch_start = (
            (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
            if latest
            else start_arg
        )
        incoming = []
        if fetch_start <= end:
            try:
                incoming = fetch_history(code, fetch_start, end, args.auto_adjust, args.retries)
            except Exception as exc:
                print(f"[{index}/{len(symbols)}] {code} 下载失败：{exc}")
                if not existing:
                    continue
            time.sleep(0.12)

        by_date = {normalize_date(row.get("trade_date")): row for row in existing}
        by_date.update({normalize_date(row.get("trade_date")): row for row in incoming})
        records = enrich_records([by_date[key] for key in sorted(by_date) if key])
        if not records:
            print(f"[{index}/{len(symbols)}] {code} 无数据，跳过")
            continue
        payload = {
            "ts_code": code,
            "name": name,
            "provider_symbol": yahoo_symbol(code),
            "data_source": "yfinance",
            "auto_adjust": args.auto_adjust,
            "start_date": normalize_date(records[0].get("trade_date")),
            "end_date": normalize_date(records[-1].get("trade_date")),
            "records": records,
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(json_clean(payload), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        completed.append(payload)
        print(f"[{index}/{len(symbols)}] {code}: {len(records)} 条")

    if not completed:
        raise RuntimeError("没有可写入的美股数据，未更新汇总文件")
    market_payloads = []
    for path in sorted(STOCKS_DIR.glob("*.json")):
        payload = read_payload(path)
        if payload.get("records"):
            market_payloads.append({
                "ts_code": str(payload.get("ts_code") or path.stem),
                "name": str(payload.get("name") or path.stem),
                "records": payload["records"],
            })
    write_market_files(market_payloads, start_arg, end, args.auto_adjust)
    print(f"yfinance 美股更新完成：{len(completed)} 只，目录：{DATA_DIR}")


if __name__ == "__main__":
    main()
