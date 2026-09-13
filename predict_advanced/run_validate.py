from __future__ import annotations

import argparse
import json
from pathlib import Path
from .data import validate_rows, features, prediction_cutoff
from .runner import load_indices, ROOT


def main():
    parser = argparse.ArgumentParser(description="Validate point-in-time market data, without model calls")
    parser.add_argument("--date", required=True)
    parser.add_argument("--lookback", type=int, default=30)
    args = parser.parse_args()
    for item in load_indices(ROOT / "data" / "Astocks" / "indices"):
        rows, quality = validate_rows(item["records"], args.date, args.lookback, prediction_cutoff(args.date))
        print(json.dumps({"ts_code": item["ts_code"], "quality": quality, "features": features(rows)}, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
