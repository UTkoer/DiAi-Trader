#!/usr/bin/env python3
"""Incrementally download U.S. stock daily data with AkShare.

AkShare/Eastmoney symbols such as ``105.AAPL`` are normalized to ``AAPL``
when files are written, so this script can share the ``data/USstocks`` layout
with ``get_us_stocks_data.py``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("US_STOCKS_DATA_DIR", ROOT / "USstocks"))
STOCKS_DIR = DATA_DIR / "stocks"
DAILY_DIR = DATA_DIR / "daily"
START_DEFAULT = "20250101"
HISTORY_COLUMNS = {
    "日期": "trade_date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "vol",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_change",
    "涨跌额": "change",
    "换手率": "turnover_rate",
}


def normalize_date(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "").replace("/", "")[:8]


def canonical_symbol(value: str) -> str:
    """Convert an Eastmoney symbol such as 105.AAPL to AAPL."""
    symbol = str(value or "").strip().upper()
    prefix, separator, suffix = symbol.partition(".")
    return suffix if separator and prefix.isdigit() else symbol


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


def read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle).get("records") or []
    except (OSError, ValueError, json.JSONDecodeError):
        return []


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


def fetch_spot_stocks() -> pd.DataFrame:
    frame = ak.stock_us_spot_em()
    if frame is None or frame.empty or "代码" not in frame:
        raise RuntimeError("AkShare stock_us_spot_em 未返回美股列表")
    stocks = pd.DataFrame(
        {
            "provider_symbol": frame["代码"].astype(str).str.upper(),
            "ts_code": frame["代码"].map(canonical_symbol),
            "name": frame["名称"].astype(str) if "名称" in frame else frame["代码"].astype(str),
        }
    )
    return stocks.drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)


def resolve_stocks(symbols: list[str] | None, symbols_file: str | None) -> pd.DataFrame:
    requested = list(symbols or [])
    if symbols_file:
        requested.extend(load_symbols_file(Path(symbols_file)))
    requested = sorted(set(symbol.upper() for symbol in requested if symbol))

    try:
        stocks = fetch_spot_stocks()
    except Exception:
        provider_symbols = [symbol for symbol in requested if canonical_symbol(symbol) != symbol]
        if not provider_symbols:
            raise
        return pd.DataFrame(
            {
                "provider_symbol": provider_symbols,
                "ts_code": [canonical_symbol(symbol) for symbol in provider_symbols],
                "name": [canonical_symbol(symbol) for symbol in provider_symbols],
            }
        )

    if not requested:
        return stocks
    requested_canonical = {canonical_symbol(symbol) for symbol in requested}
    selected = stocks[stocks["ts_code"].isin(requested_canonical)].copy()
    found = set(selected["ts_code"])
    missing = sorted(requested_canonical - found)
    if missing:
        print(f"未在 AkShare 美股列表中找到，已跳过：{', '.join(missing)}")
    if selected.empty:
        raise RuntimeError("指定的美股代码均未在 AkShare 列表中找到")
    return selected.reset_index(drop=True)


def fetch_history(provider_symbol: str, start: str, end: str, adjust: str, retries: int) -> list[dict]:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            frame = ak.stock_us_hist(
                symbol=provider_symbol,
                period="daily",
                start_date=start,
                end_date=end,
                adjust=adjust,
            )
            if frame is None or frame.empty:
                return []
            frame = frame.rename(columns=HISTORY_COLUMNS)
            required = {"trade_date", "open", "high", "low", "close", "vol"}
            missing = required - set(frame.columns)
            if missing:
                raise RuntimeError(f"AkShare 历史行情缺少字段：{', '.join(sorted(missing))}")
            frame["trade_date"] = frame["trade_date"].map(normalize_date)
            numeric_columns = [column for column in HISTORY_COLUMNS.values() if column != "trade_date"]
            for column in numeric_columns:
                if column in frame:
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
            return frame.sort_values("trade_date").to_dict("records")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(attempt * 2)
    raise RuntimeError(f"AkShare 历史行情请求失败：{last_error}")


def enrich_records(records: list[dict]) -> list[dict]:
    if not records:
        return []
    frame = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "vol", "amount"):
        if column not in frame:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
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


def write_market_files(stocks: list[dict], start: str, end: str) -> None:
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
                "source": "AkShare",
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

    parser = argparse.ArgumentParser(description="使用 AkShare 增量下载美股日线行情")
    parser.add_argument("--start", default=START_DEFAULT, help="开始日期，格式 YYYYMMDD")
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"), help="结束日期，格式 YYYYMMDD")
    parser.add_argument("--symbols", nargs="*", help="只下载指定代码，例如 AAPL MSFT NVDA")
    parser.add_argument("--symbols-file", help="从 JSON 文件读取代码列表")
    parser.add_argument("--adjust", choices=("raw", "qfq", "hfq"), default="raw", help="复权方式")
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

    start_arg = normalize_date(args.start)
    end = normalize_date(args.end)
    adjust = "" if args.adjust == "raw" else args.adjust
    stocks = resolve_stocks(args.symbols, args.symbols_file)
    if args.limit:
        stocks = stocks.head(args.limit)

    completed = []
    for index, stock in stocks.iterrows():
        code = str(stock["ts_code"])
        provider_symbol = str(stock["provider_symbol"])
        name = str(stock.get("name") or code)
        path = STOCKS_DIR / f"{code}.json"
        existing = read_records(path)
        latest = max((normalize_date(row.get("trade_date")) for row in existing), default="")
        fetch_start = (
            (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
            if latest
            else start_arg
        )
        incoming = []
        if fetch_start <= end:
            try:
                incoming = fetch_history(provider_symbol, fetch_start, end, adjust, args.retries)
            except Exception as exc:
                print(f"[{index + 1}/{len(stocks)}] {code} 下载失败：{exc}")
                if not existing:
                    continue
            time.sleep(0.12)

        by_date = {normalize_date(row.get("trade_date")): row for row in existing}
        by_date.update({normalize_date(row.get("trade_date")): row for row in incoming})
        records = enrich_records([by_date[key] for key in sorted(by_date) if key])
        if not records:
            print(f"[{index + 1}/{len(stocks)}] {code} 无数据，跳过")
            continue
        payload = {
            "ts_code": code,
            "name": name,
            "provider_symbol": provider_symbol,
            "data_source": "AkShare",
            "adjust": args.adjust,
            "start_date": normalize_date(records[0].get("trade_date")),
            "end_date": normalize_date(records[-1].get("trade_date")),
            "records": records,
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(json_clean(payload), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        completed.append(payload)
        print(f"[{index + 1}/{len(stocks)}] {code}: {len(records)} 条")

    if not completed:
        raise RuntimeError("没有可写入的美股数据，未更新汇总文件")
    write_market_files(completed, start_arg, end)
    print(f"AkShare 美股更新完成：{len(completed)} 只，复权方式：{args.adjust}，目录：{DATA_DIR}")


if __name__ == "__main__":
    main()
