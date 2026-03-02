import json
import sys

pos_path = 'data/agent_data_astock/ZSG_17_day/gpt-oss-120b/position/position.jsonl'
merged_path = 'data/a_stock_data/ZSG_17_day/merged.jsonl'
target_date = '2026-01-16'
PRICE_FIELDS = ['4. sell price', '4. close', '4. sell', 'close', '1. buy price']


def load_position(pos_path, date):
    try:
        with open(pos_path, 'r', encoding='utf-8') as f:
            for line in f:
                obj = json.loads(line)
                if obj.get('date','').startswith(date):
                    positions = obj.get('positions')
                    return obj, positions
    except FileNotFoundError:
        print('Position file missing', pos_path)
    return None, None


def load_merged(merged_path):
    m = {}
    try:
        with open(merged_path, 'r', encoding='utf-8') as f:
            for line in f:
                obj = json.loads(line)
                # symbol location
                sym = None
                md = obj.get('Meta Data')
                if md and isinstance(md, dict):
                    sym = md.get('2. Symbol') or md.get('2.1. Symbol')
                if not sym:
                    # try other heuristics
                    if '2. Symbol' in obj:
                        sym = obj['2. Symbol']
                # find a time-series key
                ts = None
                for k in obj.keys():
                    if 'Time Series' in k or 'TimeSeries' in k or 'time' in k.lower():
                        ts = obj[k]
                        break
                if sym and ts:
                    m[sym] = ts
        return m
    except FileNotFoundError:
        print('Merged file missing', merged_path)
        return m


def check(positions, merged, date):
    missing = []
    ok = {}
    for sym, qty in positions.items():
        if sym == 'CASH':
            continue
        if not qty:
            continue
        series = merged.get(sym)
        if series is None:
            missing.append((sym, 'no_symbol'))
            continue
        entry = None
        if date in series:
            entry = series[date]
        else:
            keys = [k for k in series.keys() if k.startswith(date)]
            if keys:
                entry = series[sorted(keys).pop()]
        if entry is None:
            # try nearest previous
            keys = sorted([k for k in series.keys() if len(k)>=10 and k[:10] <= date])
            if keys:
                entry = series[keys[-1]]
                ok[sym] = ('fallback_date', keys[-1], entry)
                continue
            missing.append((sym, 'no_date'))
            continue
        # find price field
        found = None
        for pf in PRICE_FIELDS:
            if isinstance(entry, dict) and pf in entry and entry[pf] not in (None, ''):
                try:
                    val = float(entry[pf])
                    found = (pf, val)
                    break
                except Exception:
                    continue
        if found:
            ok[sym] = ('ok', found)
        else:
            # entry exists but no usable field
            missing.append((sym, 'no_price_field', entry))
    return missing, ok


if __name__ == '__main__':
    pos_obj, positions = load_position(pos_path, target_date)
    if not positions:
        print('No positions for', target_date)
        sys.exit(0)
    print('Positions keys count:', len(positions))
    # filter positive holdings
    held = {s:positions[s] for s in positions if s!='CASH' and positions[s] and positions[s]!=0}
    print('Held symbols on', target_date, '->', list(held.keys()))
    merged = load_merged(merged_path)
    print('Merged symbols loaded:', len(merged))
    missing, ok = check(held, merged, target_date)
    print('\nOK count:', len(ok))
    for s,v in ok.items():
        print(s, '=>', v)
    print('\nMissing count:', len(missing))
    for item in missing:
        print(item)
