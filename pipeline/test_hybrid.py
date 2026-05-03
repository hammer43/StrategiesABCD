import json
import pandas as pd
from datetime import datetime, timezone
from ml.hybrid_scorer import hybrid_score
from candle_daemon import load_candles, get_htf_bias

def to_df(candles):
    df = pd.DataFrame(candles)
    df['datetime'] = pd.to_datetime(df['datetime'])
    for col in ['open','high','low','close']:
        df[col] = df[col].astype(float)
    return df.sort_values('datetime').reset_index(drop=True)

def detect_sweep(df):
    if len(df) < 20:
        return None
    last = df.iloc[-1]
    prev_low = df['low'].rolling(20).min().iloc[-10]
    if last['low'] < prev_low and last['close'] > last['open']:
        cr = last['high'] - last['low']
        return {
            'depth': round((prev_low-last['low'])*10,1),
            'wick_ratio': round((last['open']-last['low'])/cr,2) if cr>0 else 0,
            'body_ratio': round((last['close']-last['open'])/cr,2) if cr>0 else 0,
            'sweep_strength': round((prev_low-last['low'])*10*(last['open']-last['low'])/cr,2) if cr>0 else 0
        }
    return None

def detect_fvg(df):
    for i in range(len(df)-1, 1, -1):
        c1 = df.iloc[i-2]
        c3 = df.iloc[i]
        if c1['high'] < c3['low']:
            return {
                'top': c3['low'], 'bottom': c1['high'],
                'mid': (c3['low']+c1['high'])/2,
                'bullish': True, 'age': len(df)-i,
                'price_to_50pct': 0
            }
    return None

# Load from cache
df_1m  = to_df(load_candles('1m'))
df_5m  = to_df(load_candles('5m'))
df_15m = to_df(load_candles('15m'))
df_4h  = to_df(load_candles('4h'))

sweep = detect_sweep(df_5m)
fvg   = detect_fvg(df_5m)
bias  = get_htf_bias()
mitigation = fvg['bottom']+(fvg['top']-fvg['bottom'])*0.5 if fvg else None

if fvg and mitigation:
    current = float(df_1m['close'].iloc[-1])
    fvg['price_to_50pct'] = abs(current - mitigation)

current = float(df_1m['close'].iloc[-1])
print(f'Price: {current}')
print(f'Bias: {bias} | Sweep: {sweep is not None} | FVG: {fvg is not None}')
if fvg:
    print(f'FVG: {round(fvg["bottom"],2)}-{round(fvg["top"],2)} | 50%: {round(mitigation,2)} | Age: {fvg["age"]} candles')
if sweep:
    print(f'Sweep: depth={sweep["depth"]}pips strength={sweep.get("sweep_strength",0)}')

test_signal = {
    'zone_top':  round(current+2,1),
    'zone_bot':  round(current-3,1),
    'tp1':       round(current+5,1),
    'sl':        round(current-4,1),
    'high_risk': False
}

result = hybrid_score(
    test_signal, df_1m, df_5m, df_15m, df_4h,
    fvg, sweep, mitigation, bias
)

print(f'\nAction: {result["action"]} | Confidence: {result["confidence"]}%')
print(f'Components:')
for k,v in result.get('components',{}).items():
    print(f'  {k}: {v}%')
print(f'Market:')
for k,v in result.get('market',{}).items():
    print(f'  {k}: {v}')
