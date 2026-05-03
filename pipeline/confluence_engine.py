import json
import os
from datetime import datetime, timezone

CANDLE_DIR = '/root/gold-signals/pipeline/candles'

def load_candles(tf):
    path = f'{CANDLE_DIR}/{tf}.json'
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)

def detect_order_block(candles, lookback=20):
    if not candles or len(candles) < 3:
        return None
    recent = candles[-lookback:] if len(candles) > lookback else candles
    for i in range(len(recent)-2, 0, -1):
        curr = recent[i]
        next_c = recent[i+1]
        is_bearish = curr['close'] < curr['open']
        next_bullish = next_c['close'] > next_c['open']
        next_strong = (next_c['close'] - next_c['open']) > (curr['open'] - curr['close'])
        if is_bearish and next_bullish and next_strong:
            return {
                'high': curr['high'],
                'low': curr['low'],
                'mid': (curr['high'] + curr['low']) / 2,
                'age': len(recent) - i,
                'valid': True
            }
    return None

def detect_adm_phase(candles_1h, current_hour):
    phase = 'neutral'
    if 0 <= current_hour <= 4:
        phase = 'accumulation'
    elif 5 <= current_hour <= 8:
        phase = 'manipulation'
    elif 9 <= current_hour <= 12:
        phase = 'distribution_early'
    elif 13 <= current_hour <= 16:
        phase = 'distribution_peak'
    else:
        phase = 'neutral'

    sweep_detected = False
    if candles_1h and len(candles_1h) >= 5:
        recent = candles_1h[-5:]
        lows = [c['low'] for c in recent]
        prev_low = min(lows[:-1])
        last = recent[-1]
        if last['low'] < prev_low and last['close'] > last['open']:
            sweep_detected = True

    score = 0.0
    if phase == 'manipulation' and sweep_detected:
        score = 1.0
    elif phase == 'manipulation':
        score = 0.7
    elif phase == 'distribution_early' and sweep_detected:
        score = 0.85
    elif phase == 'accumulation':
        score = 0.5
    elif phase == 'distribution_peak':
        score = 0.6
    else:
        score = 0.4

    return {
        'phase': phase,
        'sweep_detected': sweep_detected,
        'score': round(score, 2),
        'description': f'{phase} sweep={sweep_detected}'
    }

def check_round_numbers(tp1, tp2, tp3, tolerance=0.5):
    score = 0.0
    round_levels = []
    for target in [tp1, tp2, tp3]:
        if target is None:
            continue
        r100 = target % 100
        r50  = target % 50
        r10  = target % 10
        if r100 < tolerance or r100 > 100 - tolerance:
            score += 1.0
            round_levels.append(round(target))
        elif r50 < tolerance or r50 > 50 - tolerance:
            score += 0.6
            round_levels.append(round(target))
        elif r10 < tolerance or r10 > 10 - tolerance:
            score += 0.3
            round_levels.append(round(target))
    targets = [t for t in [tp1, tp2, tp3] if t is not None]
    normalized = min(1.0, score / len(targets)) if targets else 0.0
    return {
        'score': round(normalized, 2),
        'round_levels': round_levels
    }

def check_session_liquidity(tp1, current_price):
    candles_15m = load_candles('15m')
    if not candles_15m or len(candles_15m) < 20:
        return {'score': 0.5}
    highs = [c['high'] for c in candles_15m[-20:]]
    lows  = [c['low']  for c in candles_15m[-20:]]
    session_high = max(highs)
    session_low  = min(lows)
    session_range = session_high - session_low
    score = 0.3
    if tp1 and abs(tp1 - session_high) < 5:
        score = 0.9
    elif session_range > 0:
        position = (current_price - session_low) / session_range
        if position < 0.4:
            score = 0.7
        elif position < 0.6:
            score = 0.5
        else:
            score = 0.3
    return {
        'score': round(score, 2),
        'session_high': round(session_high, 2),
        'session_low': round(session_low, 2)
    }

def check_post_sl_reentry():
    log_file = '/root/gold-signals/pipeline/message_log.json'
    if not os.path.exists(log_file):
        return {'score': 0.5, 'is_post_sl': False}
    with open(log_file) as f:
        logs = json.load(f)
    for msg in reversed(logs[-10:]):
        if 'SL HIT' in msg.get('text', '').upper():
            return {'score': 0.85, 'is_post_sl': True}
    return {'score': 0.5, 'is_post_sl': False}

def get_confluence_score(signal, current_price):
    tp1 = signal.get('tp1')
    tp2 = signal.get('tp2')
    tp3 = signal.get('tp3')
    zone_bot = signal.get('zone_bot')
    hour = datetime.now(timezone.utc).hour

    candles_5m  = load_candles('5m')
    candles_15m = load_candles('15m')
    candles_1h  = load_candles('1h')

    ob_5m  = detect_order_block(candles_5m)
    ob_15m = detect_order_block(candles_15m)
    adm    = detect_adm_phase(candles_1h, hour)
    rounds = check_round_numbers(tp1, tp2, tp3)
    liq    = check_session_liquidity(tp1, current_price)
    post_sl = check_post_sl_reentry()

    ob_score = 0.0
    ob_near_zone = False
    for ob in [ob_5m, ob_15m]:
        if ob and zone_bot:
            if abs(ob['mid'] - zone_bot) < 10:
                ob_score = 0.9
                ob_near_zone = True
                break
            elif abs(ob['mid'] - zone_bot) < 20:
                ob_score = 0.6

    confluence = (
        0.25 * adm['score'] +
        0.25 * ob_score +
        0.20 * rounds['score'] +
        0.20 * liq['score'] +
        0.10 * post_sl['score']
    )

    return {
        'confluence_score': round(confluence, 2),
        'ob_near_zone': ob_near_zone,
        'adm_phase': adm['phase'],
        'adm_sweep': adm['sweep_detected'],
        'components': {
            'adm':        adm['score'],
            'order_block': ob_score,
            'round_num':  rounds['score'],
            'session_liq': liq['score'],
            'post_sl':    post_sl['score']
        },
        'details': {
            'adm':        adm['description'],
            'ob_5m':      f'OB at {ob_5m["mid"]:.1f} age={ob_5m["age"]}' if ob_5m else 'No OB',
            'ob_15m':     f'OB at {ob_15m["mid"]:.1f} age={ob_15m["age"]}' if ob_15m else 'No OB',
            'rounds':     str(rounds['round_levels']),
            'session':    f'High={liq.get("session_high")} Low={liq.get("session_low")}',
            'post_sl':    'Post-SL reentry' if post_sl['is_post_sl'] else 'Fresh signal'
        }
    }

if __name__ == '__main__':
    test_signal = {
        'zone_top': 4697.0, 'zone_bot': 4691.0,
        'tp1': 4700.0, 'tp2': 4710.0,
        'tp3': 4720.0, 'sl': 4688.0
    }
    result = get_confluence_score(test_signal, 4695.0)
    print(f'Confluence: {result["confluence_score"]}')
    print(f'ADM phase: {result["adm_phase"]} sweep={result["adm_sweep"]}')
    print(f'OB near zone: {result["ob_near_zone"]}')
    print('Components:')
    for k, v in result['components'].items():
        print(f'  {k}: {v}')
    print('Details:')
    for k, v in result['details'].items():
        print(f'  {k}: {v}')
