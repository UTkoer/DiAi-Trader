#!/usr/bin/env python3
"""Incrementally download common U.S. index daily bars.

The provider order follows ``zhangxiangliang/stock-api``:
Tencent -> Sina -> Eastmoney.  Indexes are kept separate from equities and
are written as ``data/USstocks/indices/<index>.json``.
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


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("US_STOCKS_DATA_DIR", ROOT / "USstocks"))
INDICES_DIR = DATA_DIR / "indices"
START_DEFAULT = "20250101"

# Canonical names are stable in local files; provider symbols match the
# conventions used by the upstream stock-api adapters.
INDICES = {
    "DJIA": {"name": "道琼斯工业平均指数", "tencent": "DJI", "sina": "dji", "eastmoney": "DJIA"},
    "IXIC": {"name": "纳斯达克综合指数", "tencent": "IXIC", "sina": "ixic", "eastmoney": "IXIC"},
    "SPX": {"name": "标普500指数", "tencent": "INX", "sina": "inx", "eastmoney": "SPX"},
}
INDEX_ALIASES = {
    "DJI": "DJIA",
    "DOW": "DJIA",
    "NDX": "IXIC",
    "NASDAQ": "IXIC",
    "SP500": "SPX",
    "S&P500": "SPX",
}
PROVIDERS = ("tencent", "sina", "eastmoney")


def normalize_date(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "").replace("/", "")[:8]


def clean_number(value):
    try:
        number = float(value) if value not in (None, "", "-") else None
        return number if number is None or math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return default


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
        return json.loads(response.read().decode(encoding, errors="replace"))


def tencent_history(provider_symbol: str, start: str, end: str, count: int) -> list[dict]:
    code = f"us{provider_symbol}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={code},day,,,{count}"
    payload = request_json(url)
    rows = ((payload.get("data") or {}).get(code) or {}).get("day") or []
    result = []
    for row in rows:
        if len(row) < 6:
            continue
        date = normalize_date(row[0])
        if start <= date <= end:
            result.append({"trade_date": date, "open": clean_number(row[1]), "close": clean_number(row[2]), "high": clean_number(row[3]), "low": clean_number(row[4]), "vol": clean_number(row[5])})
    return result


def sina_history(provider_symbol: str, start: str, end: str, count: int) -> list[dict]:
    url = "https://quotes.sina.cn/cn/api/json_v2.php/" f"CN_MarketData.getKLineData?symbol={quote(provider_symbol)}&scale=240&ma=no&datalen={count}"
    rows = request_json(url, encoding="gb18030")
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        date = normalize_date(row.get("day"))
        if start <= date <= end:
            result.append({"trade_date": date, "open": clean_number(row.get("open")), "close": clean_number(row.get("close")), "high": clean_number(row.get("high")), "low": clean_number(row.get("low")), "vol": clean_number(row.get("volume"))})
    return result


def eastmoney_history(provider_symbol: str, start: str, end: str, count: int) -> list[dict]:
    # 100 is the Eastmoney market id used for overseas indexes in many of its
    # public endpoints; if unavailable, the next provider is attempted.
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid=100.{quote(provider_symbol)}&klt=101&fqt=0&beg={start}&end={end}"
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
        if start <= date <= end:
            result.append({"trade_date": date, "open": clean_number(fields[1]), "close": clean_number(fields[2]), "high": clean_number(fields[3]), "low": clean_number(fields[4]), "vol": clean_number(fields[5]), "amount": clean_number(fields[6])})
    return result


def fetch_history(index: str, start: str, end: str, retries: int, count: int) -> tuple[list[dict], str]:
    loaders = {"tencent": tencent_history, "sina": sina_history, "eastmoney": eastmoney_history}
    errors = []
    for provider in PROVIDERS:
        provider_symbol = INDICES[index][provider]
        for attempt in range(1, retries + 1):
            try:
                rows = loaders[provider](provider_symbol, start, end, count)
                if rows:
                    for row in rows:
                        row["source"] = provider
                    return rows, provider
                raise RuntimeError("empty response")
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, RuntimeError) as exc:
                errors.append(f"{provider}: {exc}")
                if attempt < retries:
                    time.sleep(attempt)
        print(f"{index}: {provider} unavailable, trying next provider")
    raise RuntimeError("; ".join(errors))


def load_symbols(path: Path) -> list[str]:
    payload = read_json(path, None)
    values = payload if isinstance(payload, list) else payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ValueError(f"{path} must contain a JSON array or symbols array")
    return [str(value).strip().upper() for value in values]


def canonical_symbols(values: list[str]) -> tuple[list[str], list[str]]:
    selected = []
    unsupported = []
    for value in values:
        symbol = str(value).strip().upper()
        canonical = INDEX_ALIASES.get(symbol, symbol)
        if canonical in INDICES:
            if canonical not in selected:
                selected.append(canonical)
        else:
            unsupported.append(symbol)
    return selected, sorted(set(unsupported))


def main() -> None:
    parser = argparse.ArgumentParser(description="使用 stock-api 数据源顺序增量下载美股指数")
    parser.add_argument("--output", default=str(INDICES_DIR))
    parser.add_argument("--start", default=START_DEFAULT)
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--symbols", nargs="*", help="DJIA IXIC SPX")
    parser.add_argument("--symbols-file")
    parser.add_argument("--count", type=int, default=2000)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    start, end = normalize_date(args.start), normalize_date(args.end)
    symbols = list(args.symbols or INDICES)
    if args.symbols_file:
        symbols = load_symbols(Path(args.symbols_file))
    symbols, unsupported = canonical_symbols(symbols)
    if unsupported:
        print(f"Skipping non-supported U.S. index symbols: {', '.join(unsupported)}; supported: {', '.join(INDICES)}")
    if not symbols:
        raise SystemExit("No supported U.S. index symbols found")
    for index in symbols:
        path = output / f"{index}.json"
        payload = read_json(path, {})
        records = payload.get("records") or [] if isinstance(payload, dict) else []
        latest = max((normalize_date(row.get("trade_date")) for row in records), default="")
        fetch_start = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d") if latest else start
        source = payload.get("source") if isinstance(payload, dict) else None
        if fetch_start <= end:
            try:
                incoming, source = fetch_history(index, fetch_start, end, max(1, args.retries), max(1, args.count))
                by_date = {normalize_date(row.get("trade_date")): row for row in records}
                by_date.update({normalize_date(row.get("trade_date")): row for row in incoming})
                records = [by_date[key] for key in sorted(by_date) if key]
            except Exception as exc:
                print(f"{index} failed: {exc}")
        name = INDICES[index]["name"]
        result = {"ts_code": index, "name": name, "source": source or "local", "start_date": normalize_date(records[0].get("trade_date")) if records else fetch_start, "end_date": normalize_date(records[-1].get("trade_date")) if records else "", "records": records}
        with path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        print(f"{name} ({index}): {len(records)} records ({result['source']})")


if __name__ == "__main__":
    main()
