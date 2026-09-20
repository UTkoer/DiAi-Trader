#!/usr/bin/env python3
"""Download U.S. daily bars using stock-api's provider order.

The upstream ``stock-api`` project uses ``Tencent -> Sina -> Eastmoney`` as
its automatic fallback order.  This script mirrors that order without adding
the Node package as a runtime dependency and writes the same layout as the
other market downloaders::

    data/USstocks/stocks/<symbol>.json
    data/USstocks/daily/<YYYYMMDD>.json
    data/USstocks/latest.json, dates.json, meta.json
    data/USstocks/symbols/us_stock.json

U.S. exchanges do not expose a reliable all-symbol listing through these
quote endpoints, so symbols are supplied by ``--symbols``, ``--symbols-file``
or an existing ``symbols/us_stock.json`` file.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("US_STOCKS_DATA_DIR", ROOT / "USstocks"))
STOCKS_DIR = DATA_DIR / "stocks"
DAILY_DIR = DATA_DIR / "daily"
START_DEFAULT = "20250101"
PROVIDERS = ("tencent", "sina", "eastmoney")


def normalize_date(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "").replace("/", "")[:8]


def normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if symbol.startswith("US"):
        symbol = symbol[2:]
    return symbol.replace("-", ".")


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


def read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def load_symbols(path: Path) -> list[str]:
    payload = read_json(path, None)
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get("symbols")
        if values is None and len(payload) == 1:
            values = next(iter(payload.values()))
    else:
        values = None
    if not isinstance(values, list):
        raise ValueError(f"{path} must be a JSON array or an object containing symbols")
    return sorted({normalize_symbol(value) for value in values if normalize_symbol(value)})


def request_json(url: str, *, encoding: str = "utf-8"):
    request = Request(
        url,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "DiAi-Trader/1.0",
            "Referer": "https://finance.sina.com.cn/",
        },
    )
    with urlopen(request, timeout=20) as response:
        raw = response.read()
    return json.loads(raw.decode(encoding, errors="replace"))


def number(value):
    try:
        return float(value) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def tencent_history(symbol: str, start: str, end: str, count: int) -> list[dict]:
    # This is the same endpoint and parameter layout used by stock-api.
    api_code = f"us{symbol}"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param="
        f"{api_code},day,,,{count}"
    )
    payload = request_json(url)
    rows = ((payload.get("data") or {}).get(api_code) or {}).get("day") or []
    result = []
    for row in rows:
        if len(row) < 6:
            continue
        date = normalize_date(row[0])
        if start <= date <= end:
            result.append(
                {
                    "trade_date": date,
                    "open": number(row[1]),
                    "close": number(row[2]),
                    "high": number(row[3]),
                    "low": number(row[4]),
                    "vol": number(row[5]),
                    "source": "tencent",
                }
            )
    return result


def sina_history(symbol: str, start: str, end: str, count: int) -> list[dict]:
    api_code = f"{symbol.lower()}"
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={quote(api_code)}&scale=240&ma=no&datalen={count}"
    )
    rows = request_json(url, encoding="gb18030")
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        date = normalize_date(row.get("day"))
        if start <= date <= end:
            result.append(
                {
                    "trade_date": date,
                    "open": number(row.get("open")),
                    "close": number(row.get("close")),
                    "high": number(row.get("high")),
                    "low": number(row.get("low")),
                    "vol": number(row.get("volume")),
                    "source": "sina",
                }
            )
    return result


def eastmoney_history(symbol: str, start: str, end: str, count: int) -> list[dict]:
    # Eastmoney's US market secid prefix is 105 (the same form used by
    # AkShare's stock_us_hist endpoint).
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid=105.{quote(symbol)}&klt=101&fqt=0&beg={start}&end={end}"
        "&fields1=f1&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    payload = request_json(url)
    rows = ((payload.get("data") or {}).get("klines") or [])[-count:]
    result = []
    for raw in rows:
        fields = str(raw).split(",")
        if len(fields) < 7:
            continue
        date = normalize_date(fields[0])
        result.append(
            {
                "trade_date": date,
                "open": number(fields[1]),
                "close": number(fields[2]),
                "high": number(fields[3]),
                "low": number(fields[4]),
                "vol": number(fields[5]),
                "amount": number(fields[6]),
                "source": "eastmoney",
            }
        )
    return [row for row in result if start <= row["trade_date"] <= end]


def fetch_history(symbol: str, start: str, end: str, retries: int, count: int) -> tuple[list[dict], str]:
    loaders = {
        "tencent": tencent_history,
        "sina": sina_history,
        "eastmoney": eastmoney_history,
    }
    errors = []
    for provider in PROVIDERS:
        for attempt in range(1, retries + 1):
            try:
                rows = loaders[provider](symbol, start, end, count)
                if rows:
                    return rows, provider
                raise RuntimeError("empty response")
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, RuntimeError) as exc:
                errors.append(f"{provider}: {exc}")
                if attempt < retries:
                    time.sleep(attempt)
        print(f"{symbol}: {provider} unavailable, trying next provider")
    raise RuntimeError("; ".join(errors))


def enrich_records(records: list[dict]) -> list[dict]:
    frame = pd.DataFrame(records).sort_values("trade_date").drop_duplicates("trade_date")
    for column in ("open", "high", "low", "close", "vol", "amount"):
        if column not in frame:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["amount"] = frame["amount"].fillna(frame["close"] * frame["vol"])
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
    frame["vwap"] = frame.get("vwap")
    return frame.where(pd.notna(frame), None).to_dict("records")


def write_market_files(stocks: list[dict], start: str, end: str) -> None:
    rows_by_date: dict[str, list[dict]] = {}
    symbols, name_map = [], {}
    for stock in stocks:
        code, name = stock["ts_code"], stock["name"]
        symbols.append(code)
        name_map[code] = name
        for row in stock["records"]:
            date = normalize_date(row.get("trade_date"))
            if date:
                rows_by_date.setdefault(date, []).append({**row, "ts_code": code, "name": name})
    dates = sorted(rows_by_date)
    for date, rows in rows_by_date.items():
        with (DAILY_DIR / f"{date}.json").open("w", encoding="utf-8") as handle:
            json.dump(json_clean({"date": date, "asset_count": len(rows), "data": rows}), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    latest = rows_by_date.get(dates[-1], []) if dates else []
    for filename, payload in {
        "latest.json": {"date": dates[-1] if dates else None, "asset_count": len(latest), "data": latest},
        "dates.json": {"dates": dates, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        "meta.json": {"asset_type": "US_STOCK", "stock_count": len(symbols), "symbols": symbols, "name_map": name_map, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "start_date": dates[0] if dates else start, "end_date": dates[-1] if dates else end},
    }.items():
        with (DATA_DIR / filename).open("w", encoding="utf-8") as handle:
            json.dump(json_clean(payload), handle, ensure_ascii=False, indent=2, allow_nan=False)
    with (DATA_DIR / "symbols" / "us_stock.json").open("w", encoding="utf-8") as handle:
        json.dump({"symbolname": "美股", "symbols": symbols}, handle, ensure_ascii=False, indent=2)


def main() -> None:
    global DATA_DIR, STOCKS_DIR, DAILY_DIR
    parser = argparse.ArgumentParser(description="使用 stock-api 数据源顺序增量下载美股日线")
    parser.add_argument("--start", default=START_DEFAULT)
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--symbols-file")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--count", type=int, default=5000, help="每个数据源请求的最大 K 线数量")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--output", default=str(DATA_DIR))
    args = parser.parse_args()
    DATA_DIR = Path(args.output)
    STOCKS_DIR, DAILY_DIR = DATA_DIR / "stocks", DATA_DIR / "daily"
    STOCKS_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "symbols").mkdir(parents=True, exist_ok=True)
    start, end = normalize_date(args.start), normalize_date(args.end)
    symbols = [normalize_symbol(value) for value in (args.symbols or [])]
    if args.symbols_file:
        symbols.extend(load_symbols(Path(args.symbols_file)))
    if not symbols:
        default_file = DATA_DIR / "symbols" / "us_stock.json"
        if default_file.exists():
            symbols = load_symbols(default_file)
    symbols = sorted(set(symbols))
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        raise SystemExit("请通过 --symbols 或 --symbols-file 提供美股代码")
    completed = []
    for index, symbol in enumerate(symbols, 1):
        path = STOCKS_DIR / f"{symbol}.json"
        old = read_json(path, {})
        existing = old.get("records") or [] if isinstance(old, dict) else []
        latest = max((normalize_date(row.get("trade_date")) for row in existing), default="")
        fetch_start = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d") if latest else start
        incoming = []
        source = old.get("source") if isinstance(old, dict) else None
        if fetch_start <= end:
            try:
                incoming, source = fetch_history(symbol, fetch_start, end, max(1, args.retries), max(1, args.count))
            except Exception as exc:
                print(f"[{index}/{len(symbols)}] {symbol} failed: {exc}")
                if not existing:
                    continue
        records = enrich_records(existing + incoming)
        if not records:
            continue
        payload = {"ts_code": symbol, "name": symbol, "source": source or "local", "start_date": records[0]["trade_date"], "end_date": records[-1]["trade_date"], "records": records}
        with path.open("w", encoding="utf-8") as handle:
            json.dump(json_clean(payload), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        completed.append(payload)
        print(f"[{index}/{len(symbols)}] {symbol}: {len(records)} rows ({source or 'local'})")
    write_market_files(completed, start, end)


if __name__ == "__main__":
    main()
