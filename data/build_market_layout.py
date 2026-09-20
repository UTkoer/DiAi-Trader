#!/usr/bin/env python3
"""Build a complete market-data layout from per-symbol JSON files."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


RAW_FIELDS = {
    "ts_code", "name", "trade_date", "date", "open", "high", "low", "close",
    "adj_close", "pre_close", "change", "pct_change", "pct_chg", "vol", "volume",
    "amount", "vwap",
}


def normalize_date(value) -> str:
    return str(value or "").replace("-", "").replace("/", "")[:8]


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
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def factor_payload(code: str, name: str, records: list[dict]) -> dict:
    rows = []
    factor_names = set()
    for record in records:
        factors = {key: value for key, value in record.items() if key not in RAW_FIELDS}
        factor_names.update(factors)
        rows.append({"trade_date": normalize_date(record.get("trade_date") or record.get("date")), "factors": factors})
    return {
        "ts_code": code,
        "name": name,
        "factor_names": sorted(factor_names),
        "records": rows,
    }


def build_layout(root: Path, asset_type: str, source: str) -> dict:
    root = root.resolve()
    stocks_dir = root / "stocks"
    indices_dir = root / "indices"
    daily_dir = root / "daily"
    factors_dir = root / "factors"
    symbols_dir = root / "symbols"
    for directory in (stocks_dir, indices_dir, daily_dir, factors_dir, symbols_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_dir = root / source
    source_files = sorted(source_dir.glob("*.json"))
    if not source_files:
        raise RuntimeError(f"没有可编排的数据文件：{source_dir}")

    temp_dir = Path(tempfile.mkdtemp(prefix=".layout_build-", dir=root))
    writers = {}
    date_counts = {}
    symbols = []
    name_map = {}
    columns = set()
    valid_files = []
    try:
        for index, path in enumerate(source_files, start=1):
            payload = read_payload(path)
            records = payload.get("records") or []
            if not records:
                continue
            code = str(payload.get("ts_code") or path.stem)
            name = str(payload.get("name") or code)
            symbols.append(code)
            name_map[code] = name
            valid_files.append((path, code))
            for record in records:
                trade_date = normalize_date(record.get("trade_date") or record.get("date"))
                if not trade_date:
                    continue
                row = {"ts_code": code, "name": name, **record, "trade_date": trade_date}
                columns.update(row)
                writer = writers.get(trade_date)
                if writer is None:
                    writer = (temp_dir / f"{trade_date}.jsonl").open("a", encoding="utf-8")
                    writers[trade_date] = writer
                writer.write(json.dumps(json_clean(row), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
                date_counts[trade_date] = date_counts.get(trade_date, 0) + 1
            with (factors_dir / f"{code}.json").open("w", encoding="utf-8") as handle:
                json.dump(json_clean(factor_payload(code, name, records)), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if index % 250 == 0 or index == len(source_files):
                print(f"编排进度：{index}/{len(source_files)}")
    finally:
        for writer in writers.values():
            writer.close()

    dates = sorted(date_counts)
    if not dates:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("单标的文件中没有有效交易日期")

    latest_payload = None
    for trade_date in dates:
        rows = []
        with (temp_dir / f"{trade_date}.jsonl").open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        payload = {"date": trade_date, "asset_count": len(rows), "data": rows}
        with (daily_dir / f"{trade_date}.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if trade_date == dates[-1]:
            latest_payload = payload

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_meta = read_payload(root / "meta.json")
    meta = {
        **existing_meta,
        "asset_type": asset_type,
        "source_kind": source,
        "asset_count": len(symbols),
        "stock_count": len(symbols) if source == "stocks" else 0,
        "index_count": len(symbols) if source == "indices" else len(list(indices_dir.glob("*.json"))),
        "symbols": symbols,
        "name_map": name_map,
        "columns": sorted(columns),
        "start_date": dates[0],
        "end_date": dates[-1],
        "updated_at": updated_at,
    }
    with (root / "dates.json").open("w", encoding="utf-8") as handle:
        json.dump({"dates": dates, "updated_at": updated_at}, handle, ensure_ascii=False, separators=(",", ":"))
    with (root / "latest.json").open("w", encoding="utf-8") as handle:
        json.dump(latest_payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with (root / "meta.json").open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
    with (symbols_dir / "all.json").open("w", encoding="utf-8") as handle:
        json.dump({"symbolname": asset_type, "symbols": symbols}, handle, ensure_ascii=False, indent=2)

    with (root / "merged.json").open("w", encoding="utf-8") as handle:
        handle.write('{"meta":')
        json.dump(meta, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write(',"columns":')
        json.dump(sorted(columns), handle, ensure_ascii=False, separators=(",", ":"))
        handle.write(',"data":{')
        first = True
        for path, code in valid_files:
            payload = read_payload(path)
            name = str(payload.get("name") or code)
            records = [{**record, "name": record.get("name") or name} for record in (payload.get("records") or [])]
            if not first:
                handle.write(",")
            first = False
            handle.write(json.dumps(code, ensure_ascii=False))
            handle.write(":")
            json.dump(json_clean(records), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        handle.write("}}")

    shutil.rmtree(temp_dir, ignore_errors=True)
    result = {
        "root": str(root),
        "source": source,
        "asset_count": len(symbols),
        "date_count": len(dates),
        "start_date": dates[0],
        "end_date": dates[-1],
    }
    print(f"完整数据结构编排完成：{result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="从单标的 JSON 生成完整市场数据目录")
    parser.add_argument("--root", required=True, help="市场数据根目录")
    parser.add_argument("--asset-type", required=True, help="写入 meta.json 的资产类型")
    parser.add_argument("--source", choices=("stocks", "indices"), default="stocks", help="编排所使用的单标的目录")
    args = parser.parse_args()
    build_layout(Path(args.root), args.asset_type, args.source)


if __name__ == "__main__":
    main()
