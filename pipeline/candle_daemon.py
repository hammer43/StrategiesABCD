import urllib.request
import json
import os
import time
from datetime import datetime, timezone

TD_KEY    = '93aa3d8b8733492eaed183c3747fe100'
SYMBOL    = 'XAU/USD'
CACHE_DIR = '/root/gold-signals/pipeline/candles'
os.makedirs(CACHE_DIR, exist_ok=True)

INTERVALS = {
    '1min':  {'file': '1m.json',  'size': 50,  'refresh': 60},
    '5min':  {'file': '5m.json',  'size': 50,  'refresh': 60},
    '15min': {'file': '15m.json', 'size': 50,  'refresh': 300},
    '1h':    {'file': '1h.json',  'size': 50,  'refresh': 900},
    '4h':    {'file': '4h.json',  'size': 20,  'refresh': 3600}
}

last_fetch = {k: 0 for k in INTERVALS}

def fetch_candles(interval, size):
    try:
        url = (f'https://api.twelvedata.com/time_series'
               f'?symbol={SYMBOL}'
               f'&interval={interval}'
               f'&outputsize={size}'
               f'&apikey={TD_KEY}')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        if 'values' not in data:
            print(f'Error {interval}: {data.get("message","unknown")}')
            return False
        candles = []
        for v in reversed(data['values']):
            candles.append({
                'datetime': v['datetime'],
                'open':  float(v['open']),
                'high':  float(v['high']),
                'low':   float(v['low']),
                'close': float(v['close'])
            })
        path = f'{CACHE_DIR}/{INTERVALS[interval]["file"]}'
        with open(path, 'w') as f:
            json.dump(candles, f)
        return True
    except Exception as e:
        print(f'Fetch error {interval}: {e}')
        return False

def load_candles(timeframe):
    files = {'1m':'1m.json','5m':'5m.json','15m':'15m.json','1h':'1h.json','4h':'4h.json'}
    path = f'{CACHE_DIR}/{files[timeframe]}'
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

def get_htf_bias():
    candles = load_candles('4h')
    if not candles or len(candles) < 6:
        return 'bullish'
    highs = [c['high'] for c in candles[-6:]]
    lows  = [c['low']  for c in candles[-6:]]
    hh = highs[-1] > highs[-3]
    hl = lows[-1]  > lows[-3]
    if hh and hl:
        return 'bullish'
    elif not hh and not hl:
        return 'bearish'
    return 'ranging'

def get_status():
    status = {'last_update': {}, 'htf_bias': None, 'candle_counts': {}}
    for tf, fname in [('1m','1m.json'),('5m','5m.json'),('15m','15m.json'),('4h','4h.json')]:
        path = f'{CACHE_DIR}/{fname}'
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            status['candle_counts'][tf] = len(data)
            status['last_update'][tf] = data[-1]['datetime'] if data else None
    status['htf_bias'] = get_htf_bias()
    return status

if __name__ == '__main__':
    print('Candle daemon starting...')
    # Initial fetch all
    for interval, cfg in INTERVALS.items():
        print(f'Fetching {interval}...')
        fetch_candles(interval, cfg['size'])
        last_fetch[interval] = time.time()
        time.sleep(2)

    status = get_status()
    print(f'Initial fetch complete')
    print(f'4H Bias: {status["htf_bias"]}')
    for tf, count in status['candle_counts'].items():
        print(f'{tf}: {count} candles, latest: {status["last_update"][tf]}')

    print('\nCandle daemon running — refreshing every minute...')

    while True:
        now = time.time()
        for interval, cfg in INTERVALS.items():
            if now - last_fetch[interval] >= cfg['refresh']:
                success = fetch_candles(interval, cfg['size'])
                if success:
                    last_fetch[interval] = now
                    candles = load_candles(interval.replace('min','m').replace('h','h'))
                    if candles:
                        latest = candles[-1]['datetime']
                        print(f'[{datetime.now(timezone.utc).strftime("%H:%M:%S")}] {interval} updated — latest: {latest}')
                time.sleep(1)

        # Write status file every minute
        status = get_status()
        status['timestamp'] = datetime.now(timezone.utc).isoformat()
        with open(f'{CACHE_DIR}/status.json', 'w') as f:
            json.dump(status, f, indent=2)

        time.sleep(30)
