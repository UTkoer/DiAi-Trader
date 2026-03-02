import json
import os

# Paths (workspace-root relative)
pos_path = 'data/agent_data_astock/ZSG_17_day/gpt-oss-120b/position/position.jsonl'
merged_path = 'data/a_stock_data/ZSG_17_day/merged.jsonl'
target_date = '2026-01-16'

# Helper to try multiple possible price fields
PRICE_FIELDS = ['4. close', '4. sell price', '4. sell', 'close', '5. close']

def load_position_symbols(pos_path, date):
    symbols = None
    try:
        with open(pos_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                # possible keys for date: 'date', 'timestamp', 'datetime'
                d = None
                for k in ('date','timestamp','datetime'):
                    if k in obj:
                        d = obj[k]
                        break
                if not d:
                    # maybe top-level time as string
                    continue
                if d.startswith(date):
                    # found the record for that date
                    # collect symbols except CASH
                    symbols = [s for s in obj.keys() if s not in ('date','timestamp','datetime','CASH')]
                    # also some position objects store holdings under 'positions' key
                    if not symbols and 'positions' in obj and isinstance(obj['positions'], dict):
                        symbols = [s for s in obj['positions'].keys() if s!='CASH']
                    return obj, symbols
    except FileNotFoundError:
        print('Position file not found:', pos_path)
    return None, symbols


def load_merged_prices(merged_path):
    # merged.jsonl: each line is a JSON object per symbol (guessing). Build dict symbol->time_series
    res = {}
    try:
        with open(merged_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                # try common shapes
                if 'symbol' in obj and ('prices' in obj or 'data' in obj or 'time_series' in obj):
                    symbol = obj.get('symbol')
                    series = obj.get('prices') or obj.get('data') or obj.get('time_series') or obj.get('timeSeries')
                    if series is None:
                        # maybe the object itself maps dates to entries
                        # remove 'symbol' then treat rest as series
                        s = {k:v for k,v in obj.items() if k!='symbol'}
                        series = s
                    res[symbol] = series
                else:
                    # maybe the JSON line is a dict where key is symbol
                    # e.g. {"000338.SZ": {"2026-01-16": {...}}, ...}
                    if len(obj)==1:
                        sym, series = next(iter(obj.items()))
                        res[sym] = series
                    else:
                        # fallback: try to find 'code' or 'ts_code'
                        if 'code' in obj:
                            symbol = obj['code']
                            series = obj.get('prices') or {k:v for k,v in obj.items() if k!='code'}
                            res[symbol] = series
        return res
    except FileNotFoundError:
        print('Merged file not found:', merged_path)
        return res


def check_symbols_on_date(symbols, merged_prices, date):
    missing = []
    details = {}
    for s in symbols:
        series = merged_prices.get(s)
        if series is None:
            missing.append(s)
            details[s] = ('no_symbol', None)
            continue
        # try exact date
        entry = None
        if isinstance(series, dict):
            # keys may be full timestamps or date strings
            if date in series:
                entry = series[date]
            else:
                # find any key starting with date
                keys = [k for k in series.keys() if k.startswith(date)]
                if keys:
                    entry = series[sorted(keys).pop()]
        # entry found?
        if entry is None:
            # try nearest previous
            keys = sorted([k for k in series.keys() if len(k)>=10 and k[:10] <= date])
            if keys:
                entry = series[keys[-1]]
                details[s] = ('fallback_prev_date', keys[-1])
            else:
                missing.append(s)
                details[s] = ('no_date', None)
                continue
        # check for price fields
        found = None
        for pf in PRICE_FIELDS:
            if isinstance(entry, dict) and pf in entry and entry[pf] not in (None, ''):
                try:
                    val = float(entry[pf])
                    found = (pf, val)
                    break
                except Exception:
                    pass
        if found:
            details[s] = ('ok', found)
        else:
            missing.append(s)
            details[s] = ('no_price_field', None)
    return missing, details


if __name__ == '__main__':
    pos_obj, symbols = load_position_symbols(pos_path, target_date)
    if pos_obj is None:
        print('No position record found for', target_date)
        sys.exit(0)
    print('Symbols in position on', target_date, '->', symbols)
    merged = load_merged_prices(merged_path)
    print('Loaded merged symbols count:', len(merged))
    missing, details = check_symbols_on_date(symbols, merged, target_date)
    print('\nMissing count:', len(missing))
    if missing:
        print('Missing symbols:')
        for s in missing:
            print('-', s, details.get(s))
    print('\nDetails:')
    for s,d in details.items():
        print(s, '=>', d)
