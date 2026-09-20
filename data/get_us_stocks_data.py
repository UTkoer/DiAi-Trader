#!/usr/bin/env python3
"""Incrementally download U.S. stock daily data from Tushare.

The output layout mirrors ``data/ETFs`` and ``data/Astocks``::

    data/USstocks/stocks/<symbol>.json
    data/USstocks/daily/<YYYYMMDD>.json
    data/USstocks/latest.json
    data/USstocks/dates.json
    data/USstocks/meta.json
    data/USstocks/symbols/us_stock.json
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
import tushare as ts
from dotenv import load_dotenv


load_dotenv()
ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("US_STOCKS_DATA_DIR", ROOT / "USstocks"))
STOCKS_DIR = DATA_DIR / "stocks"
DAILY_DIR = DATA_DIR / "daily"
START_DEFAULT = "20250101"
DAILY_FIELDS = (
    "ts_code,trade_date,close,open,high,low,pre_close,change,"
    "pct_change,vol,amount,vwap"
)
BASIC_FIELDS = "ts_code,name,enname,classify,list_date,delist_date"


def normalize_date(value) -> str:
    return str(value or "").replace("-", "").replace("/", "")


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


def enrich_records(records: list[dict]) -> list[dict]:
    """Add the technical columns used by the A-share and ETF files."""
    if not records:
        return []
    frame = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    if "pct_change" in frame and "pct_chg" not in frame:
        frame["pct_chg"] = frame["pct_change"]
    close = pd.to_numeric(frame["close"], errors="coerce")
    returns = close.pct_change()
    frame["mom_5"] = close.pct_change(5)
    frame["mom_10"] = close.pct_change(10)
    frame["mom_20"] = close.pct_change(20)
    frame["volatility_10"] = returns.rolling(10).std()
    frame["volatility_20"] = returns.rolling(20).std()
    frame["ma5"] = close.rolling(5).mean()
    frame["ma10"] = close.rolling(10).mean()
    frame["ma20"] = close.rolling(20).mean()
    frame["ma20_dev"] = (close - frame["ma20"]) / frame["ma20"]
    volume = pd.to_numeric(frame.get("vol"), errors="coerce")
    frame["vol_ratio"] = volume.rolling(5).mean() / volume.rolling(20).mean()
    frame["amount_ma5"] = pd.to_numeric(frame.get("amount"), errors="coerce").rolling(5).mean()
    return frame.where(pd.notna(frame), None).to_dict("records")


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


def fetch_listed_stocks(pro, batch_size: int = 6000) -> pd.DataFrame:
    frames = []
    offset = 0
    while True:
        frame = pro.us_basic(offset=offset, limit=batch_size, fields=BASIC_FIELDS)
        if frame is None or frame.empty:
            break
        frames.append(frame)
        if len(frame) < batch_size:
            break
        offset += batch_size
        time.sleep(0.2)
    if not frames:
        raise RuntimeError("Tushare us_basic 未返回美股列表")
    stocks = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    if "delist_date" in stocks:
        delisted = stocks["delist_date"].fillna("").astype(str).str.strip()
        stocks = stocks[delisted.eq("")]
    return stocks.sort_values("ts_code").reset_index(drop=True)


def resolve_stocks(pro, symbols: list[str] | None, symbols_file: str | None) -> pd.DataFrame:
    requested = list(symbols or [])
    if symbols_file:
        requested.extend(load_symbols_file(Path(symbols_file)))
    requested = sorted(set(symbol.upper() for symbol in requested if symbol))
    if not requested:
        return fetch_listed_stocks(pro)

    try:
        basic = fetch_listed_stocks(pro)
        basic = basic[basic["ts_code"].astype(str).str.upper().isin(requested)].copy()
    except Exception as exc:
        print(f"获取美股基础信息失败，将使用输入代码继续下载：{exc}")
        basic = pd.DataFrame()
    found = set(basic["ts_code"].astype(str).str.upper()) if not basic.empty else set()
    missing = [symbol for symbol in requested if symbol not in found]
    if missing:
        fallback = pd.DataFrame({"ts_code": missing, "name": missing, "enname": missing})
        basic = pd.concat([basic, fallback], ignore_index=True)
    return basic.drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)


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
        payload = {"date": trade_date, "asset_count": len(rows), "data": rows}
        with (DAILY_DIR / f"{trade_date}.json").open("w", encoding="utf-8") as handle:
            json.dump(json_clean(payload), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    latest_rows = rows_by_date.get(dates[-1], []) if dates else []
    latest_payload = {
        "date": dates[-1] if dates else None,
        "asset_count": len(latest_rows),
        "data": latest_rows,
    }
    with (DATA_DIR / "latest.json").open("w", encoding="utf-8") as handle:
        json.dump(json_clean(latest_payload), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
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
        json.dump(
            {"symbolname": "美股", "symbols": symbols},
            handle,
            ensure_ascii=False,
            indent=2,
        )


def main() -> None:
    global DATA_DIR, STOCKS_DIR, DAILY_DIR

    parser = argparse.ArgumentParser(description="增量下载美股日线行情")
    parser.add_argument("--start", default=START_DEFAULT, help="开始日期，格式 YYYYMMDD")
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"), help="结束日期，格式 YYYYMMDD")
    parser.add_argument("--token", default=os.getenv("TUSHARE_TOKEN"), help="默认读取 TUSHARE_TOKEN")
    parser.add_argument("--symbols", nargs="*", help="只下载指定代码，例如 AAPL MSFT NVDA")
    parser.add_argument("--symbols-file", help="从 JSON 文件读取代码列表")
    parser.add_argument("--limit", type=int, default=0, help="仅调试时限制下载数量，0 表示不限制")
    parser.add_argument("--output", default=DATA_DIR, help="数据输出目录")
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("TUSHARE_TOKEN is required")

    DATA_DIR = Path(args.output)
    STOCKS_DIR = DATA_DIR / "stocks"
    DAILY_DIR = DATA_DIR / "daily"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STOCKS_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    start_arg = normalize_date(args.start)
    end = normalize_date(args.end)
    pro = ts.pro_api(args.token)
    stocks = resolve_stocks(pro, args.symbols, args.symbols_file)
    if args.limit:
        stocks = stocks.head(args.limit)

    completed = []
    for index, stock in stocks.iterrows():
        code = str(stock["ts_code"])
        name = str(stock.get("name") or stock.get("enname") or code)
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
                frame = pro.us_daily(
                    ts_code=code,
                    start_date=fetch_start,
                    end_date=end,
                    fields=DAILY_FIELDS,
                )
                if frame is not None and not frame.empty:
                    incoming = frame.sort_values("trade_date").to_dict("records")
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
            "start_date": normalize_date(records[0].get("trade_date")),
            "end_date": normalize_date(records[-1].get("trade_date")),
            "records": records,
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(json_clean(payload), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        completed.append(payload)
        print(f"[{index + 1}/{len(stocks)}] {code}: {len(records)} 条")

    write_market_files(completed, start_arg, end)
    print(f"美股更新完成：{len(completed)} 只，目录：{DATA_DIR}")


if __name__ == "__main__":
    main()
