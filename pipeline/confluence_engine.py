import json
import os
from datetime import datetime, timezone

CANDLE_DIR = '/root/gold-signals/pipeline/candles'

# ── CANDLE LOADER ─────────────────────────────────────────
def load_candles(tf):
    path = f'{CANDLE_DIR}/{tf}.json'
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return []

# ── EMA CALCULATOR ────────────────────────────────────────
def calc_ema(prices, period):
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 2)

# ── EMA CONFLUENCE ────────────────────────────────────────
def check_ema_confluence(zone_top, zone_bot, current_price):
    """
    Check if zone aligns with key EMAs.
    Uses 1H candles for 20/50 EMA, 4H for 200 EMA.
    """
    candles_1h = load_candles('1h')
    candles_4h = load_candles('4h')

    if not candles_1h or len(candles_1h) < 20:
        return {'score': 0.5, 'details': 'Insufficient candles'}

    closes_1h = [c['close'] for c in candles_1h]
    closes_4h = [c['close'] for c in candles_4h] if candles_4h else []

    ema20  = calc_ema(closes_1h, 20)
    ema50  = calc_ema(closes_1h, 50) if len(closes_1h) >= 50 else ema20
    ema200 = calc_ema(closes_4h, 200) if len(closes_4h) >= 200 else 0

    score   = 0.0
    reasons = []

    # Zone overlaps with 20 EMA (1H) — short term support
    if zone_bot <= ema20 <= zone_top:
        score += 0.35
        reasons.append(f'20EMA@{ema20}')

    # Zone overlaps with 50 EMA (1H) — medium term support
    if zone_bot <= ema50 <= zone_top:
        score += 0.35
        reasons.append(f'50EMA@{ema50}')

    # Price above 200 EMA (4H) → bullish macro bias
    if ema200 and current_price > ema200:
        score += 0.20
        reasons.append(f'above200EMA@{ema200}')

    # Price near 20 EMA (within 5 pips) — bouncing off EMA
    if abs(current_price - ema20) < 5:
        score += 0.10
        reasons.append(f'near20EMA')

    return {
        'score':   round(min(score, 1.0), 2),
        'ema20':   ema20,
        'ema50':   ema50,
        'ema200':  ema200,
        'details': ', '.join(reasons) if reasons else 'No EMA confluence'
    }

# ── ASIAN SESSION RANGE / ORB ─────────────────────────────
def get_asian_session_range(candles_1h):
    """
    Asian session = 00:00-07:00 UTC
    Returns (asia_high, asia_low) for current day
    """
    if not candles_1h:
        return None, None
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        asian = [
            c for c in candles_1h
            if c.get('time', '').startswith(today)
            and 0 <= int(c['time'][11:13]) < 7
        ]
        if not asian:
            # Fallback: last 7 candles if no today data
            asian = candles_1h[-7:]
        asia_high = max(c['high'] for c in asian)
        asia_low  = min(c['low']  for c in asian)
        return round(asia_high, 2), round(asia_low, 2)
    except:
        return None, None

def check_orb_confluence(zone_top, zone_bot, current_price):
    """
    Check zone vs Asian session ORB levels.
    Buy zones below Asia Low = strong confluence.
    """
    candles_1h = load_candles('1h')
    asia_high, asia_low = get_asian_session_range(candles_1h)

    if not asia_high or not asia_low:
        return {'score': 0.5, 'details': 'No Asian session data'}

    score   = 0.0
    reasons = []

    # Buy zone entirely below Asia Low = price swept session low
    if zone_top < asia_low:
        score += 0.8
        reasons.append(f'Zone below AsiaLow@{asia_low}')

    # Zone overlaps with Asia Low = key level
    elif zone_bot <= asia_low <= zone_top:
        score += 0.6
        reasons.append(f'Zone at AsiaLow@{asia_low}')

    # Price below Asia Low = manipulation sweep
    if current_price < asia_low:
        score += 0.2
        reasons.append('Price below AsiaLow (sweep)')

    # Zone near Asia High = potential resistance (bearish)
    if abs(zone_top - asia_high) < 5:
        score -= 0.2
        reasons.append(f'Near AsiaHigh@{asia_high} (resistance)')

    return {
        'score':      round(min(max(score, 0.0), 1.0), 2),
        'asia_high':  asia_high,
        'asia_low':   asia_low,
        'details':    ', '.join(reasons) if reasons else f'AsiaH={asia_high} AsiaL={asia_low}'
    }

# ── ADR (Average Daily Range) ─────────────────────────────
def check_adr_exhaustion(current_price):
    """
    Gold ADR typically 10000-11000 pips (100-110 price points).
    If today's range > 50% ADR used → expect reversal.
    If < 30% ADR used → room to run.
    """
    candles_1h = load_candles('1h')
    candles_4h = load_candles('4h')

    # Calculate ADR from last 14 days of 4H candles
    ADR = 100.0  # default ~10000 pips
    if candles_4h and len(candles_4h) >= 56:  # 14 days × 6 4H candles
        daily_ranges = []
        for i in range(0, min(56, len(candles_4h)-4), 6):
            day = candles_4h[-(i+6):-i] if i > 0 else candles_4h[-6:]
            if day:
                daily_ranges.append(max(c['high'] for c in day) - min(c['low'] for c in day))
        if daily_ranges:
            ADR = sum(daily_ranges) / len(daily_ranges)

    # Today's range so far
    today_range = 0
    if candles_1h:
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            today_candles = [c for c in candles_1h if c.get('time', '').startswith(today)]
            if today_candles:
                today_high = max(c['high'] for c in today_candles)
                today_low  = min(c['low']  for c in today_candles)
                today_range = today_high - today_low
        except:
            pass

    exhaustion = today_range / ADR if ADR > 0 else 0

    if exhaustion > 0.75:
        score = 0.3   # ADR mostly used → risky
        label = f'ADR {int(exhaustion*100)}% used — exhausted'
    elif exhaustion > 0.50:
        score = 0.6   # Half used → moderate
        label = f'ADR {int(exhaustion*100)}% used — moderate'
    else:
        score = 0.9   # Plenty of room
        label = f'ADR {int(exhaustion*100)}% used — room to run'

    return {
        'score':       round(score, 2),
        'adr':         round(ADR, 1),
        'today_range': round(today_range, 1),
        'exhaustion':  round(exhaustion, 2),
        'details':     label
    }

# ── FVG DETECTION ─────────────────────────────────────────
def detect_fvg(candles, lookback=20):
    """
    Fair Value Gap = gap between candle[i-1].high and candle[i+1].low
    (for bullish FVG on displacement candle)
    """
    if not candles or len(candles) < 3:
        return None

    recent = candles[-lookback:] if len(candles) > lookback else candles

    for i in range(len(recent)-2, 1, -1):
        prev = recent[i-1]
        curr = recent[i]
        nxt  = recent[i+1]

        # Bullish FVG: gap between prev high and next low
        if nxt['low'] > prev['high']:
            fvg_low  = prev['high']
            fvg_high = nxt['low']
            fvg_mid  = (fvg_high + fvg_low) / 2
            return {
                'high':  round(fvg_high, 2),
                'low':   round(fvg_low, 2),
                'mid':   round(fvg_mid, 2),
                'size':  round((fvg_high - fvg_low) * 10, 1),
                'age':   len(recent) - i,
                'valid': True
            }
    return None

def check_fvg_confluence(zone_top, zone_bot):
    """
    Check if zone aligns with a Fair Value Gap.
    FVG within zone = high probability entry.
    """
    candles_5m  = load_candles('5m')
    candles_15m = load_candles('15m')

    fvg_5m  = detect_fvg(candles_5m)
    fvg_15m = detect_fvg(candles_15m)

    score   = 0.0
    reasons = []

    for tf, fvg in [('5m', fvg_5m), ('15m', fvg_15m)]:
        if not fvg:
            continue
        # FVG midpoint inside zone
        if zone_bot <= fvg['mid'] <= zone_top:
            score += 0.5
            reasons.append(f'FVG-{tf} mid@{fvg["mid"]}')
        # FVG overlaps zone
        elif fvg['low'] < zone_top and fvg['high'] > zone_bot:
            score += 0.3
            reasons.append(f'FVG-{tf} overlap')

    return {
        'score':   round(min(score, 1.0), 2),
        'fvg_5m':  fvg_5m,
        'fvg_15m': fvg_15m,
        'details': ', '.join(reasons) if reasons else 'No FVG confluence'
    }

# ── ORDER BLOCK DETECTION (existing, enhanced) ────────────
def detect_order_block(candles, lookback=20):
    if not candles or len(candles) < 3:
        return None
    recent = candles[-lookback:] if len(candles) > lookback else candles
    for i in range(len(recent)-2, 0, -1):
        curr   = recent[i]
        next_c = recent[i+1]
        is_bearish   = curr['close'] < curr['open']
        next_bullish = next_c['close'] > next_c['open']
        next_strong  = (next_c['close'] - next_c['open']) > (curr['open'] - curr['close'])
        if is_bearish and next_bullish and next_strong:
            return {
                'high': curr['high'],
                'low':  curr['low'],
                'mid':  (curr['high'] + curr['low']) / 2,
                'age':  len(recent) - i,
                'valid': True
            }
    return None

# ── ADM PHASE (existing) ──────────────────────────────────
def detect_adm_phase(candles_1h, current_hour):
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
        recent   = candles_1h[-5:]
        lows     = [c['low'] for c in recent]
        prev_low = min(lows[:-1])
        last     = recent[-1]
        if last['low'] < prev_low and last['close'] > last['open']:
            sweep_detected = True

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
        'phase':          phase,
        'sweep_detected': sweep_detected,
        'score':          round(score, 2),
        'description':    f'{phase} sweep={sweep_detected}'
    }

# ── ROUND NUMBERS (existing) ──────────────────────────────
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
    targets    = [t for t in [tp1, tp2, tp3] if t is not None]
    normalized = min(1.0, score / len(targets)) if targets else 0.0
    return {'score': round(normalized, 2), 'round_levels': round_levels}

# ── SESSION LIQUIDITY (existing) ──────────────────────────
def check_session_liquidity(tp1, current_price):
    candles_15m = load_candles('15m')
    if not candles_15m or len(candles_15m) < 20:
        return {'score': 0.5}
    highs = [c['high'] for c in candles_15m[-20:]]
    lows  = [c['low']  for c in candles_15m[-20:]]
    session_high  = max(highs)
    session_low   = min(lows)
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
        'score':        round(score, 2),
        'session_high': round(session_high, 2),
        'session_low':  round(session_low, 2)
    }

# ── POST-SL REENTRY (existing) ────────────────────────────
def check_post_sl_reentry():
    log_file = '/root/gold-signals/pipeline/message_log.json'
    if not os.path.exists(log_file):
        return {'score': 0.5, 'is_post_sl': False}
    try:
        with open(log_file) as f:
            logs = json.load(f)
        for msg in reversed(logs[-10:]):
            if 'SL HIT' in msg.get('text', '').upper():
                return {'score': 0.85, 'is_post_sl': True}
    except:
        pass
    return {'score': 0.5, 'is_post_sl': False}

# ── MASTER CONFLUENCE SCORE ───────────────────────────────
def get_confluence_score(signal, current_price):
    """
    Enhanced confluence engine with:
    - Order Block (OB) detection
    - ADM phase (Accumulation/Distribution/Manipulation)
    - EMA confluence (20/50/200)
    - ORB (Asian session Opening Range Breakout)
    - ADR exhaustion (Average Daily Range)
    - FVG (Fair Value Gap)
    - Round numbers
    - Session liquidity
    - Post-SL reentry

    Weights:
    ADM:         0.15
    OB:          0.15
    EMA:         0.15
    ORB:         0.15
    ADR:         0.10
    FVG:         0.10
    Round nums:  0.10
    Session liq: 0.05
    Post-SL:     0.05
    """
    tp1      = signal.get('tp1')
    tp2      = signal.get('tp2')
    tp3      = signal.get('tp3')
    zone_top = signal.get('zone_top')
    zone_bot = signal.get('zone_bot')
    hour     = datetime.now(timezone.utc).hour

    candles_1h  = load_candles('1h')
    candles_5m  = load_candles('5m')
    candles_15m = load_candles('15m')

    # Run all checks
    adm     = detect_adm_phase(candles_1h, hour)
    ema     = check_ema_confluence(zone_top, zone_bot, current_price)
    orb     = check_orb_confluence(zone_top, zone_bot, current_price)
    adr     = check_adr_exhaustion(current_price)
    fvg     = check_fvg_confluence(zone_top, zone_bot)
    rounds  = check_round_numbers(tp1, tp2, tp3)
    liq     = check_session_liquidity(tp1, current_price)
    post_sl = check_post_sl_reentry()

    # OB detection
    ob_5m  = detect_order_block(candles_5m)
    ob_15m = detect_order_block(candles_15m)
    ob_score     = 0.0
    ob_near_zone = False
    for ob in [ob_5m, ob_15m]:
        if ob and zone_bot:
            if abs(ob['mid'] - zone_bot) < 10:
                ob_score     = 0.9
                ob_near_zone = True
                break
            elif abs(ob['mid'] - zone_bot) < 20:
                ob_score = 0.6

    # Weighted confluence score
    confluence = (
        0.15 * adm['score']    +
        0.15 * ob_score        +
        0.15 * ema['score']    +
        0.15 * orb['score']    +
        0.10 * adr['score']    +
        0.10 * fvg['score']    +
        0.10 * rounds['score'] +
        0.05 * liq['score']    +
        0.05 * post_sl['score']
    )

    # Bonus: multiple confluences
    high_scores = sum(1 for s in [
        adm['score'], ob_score, ema['score'],
        orb['score'], fvg['score']
    ] if s >= 0.7)

    if high_scores >= 3:
        confluence = min(confluence * 1.15, 1.0)  # 15% bonus

    return {
        'confluence_score': round(confluence, 2),
        'ob_near_zone':     ob_near_zone,
        'adm_phase':        adm['phase'],
        'adm_sweep':        adm['sweep_detected'],
        'high_confluences': high_scores,
        'components': {
            'adm':         adm['score'],
            'order_block': ob_score,
            'ema':         ema['score'],
            'orb':         orb['score'],
            'adr':         adr['score'],
            'fvg':         fvg['score'],
            'round_num':   rounds['score'],
            'session_liq': liq['score'],
            'post_sl':     post_sl['score']
        },
        'details': {
            'adm':      adm['description'],
            'ema':      ema['details'],
            'orb':      orb['details'],
            'adr':      adr['details'],
            'fvg':      fvg['details'],
            'ob_5m':    f'OB@{ob_5m["mid"]:.1f} age={ob_5m["age"]}' if ob_5m else 'No OB',
            'ob_15m':   f'OB@{ob_15m["mid"]:.1f} age={ob_15m["age"]}' if ob_15m else 'No OB',
            'rounds':   str(rounds['round_levels']),
            'session':  f'H={liq.get("session_high")} L={liq.get("session_low")}',
            'post_sl':  'Post-SL reentry' if post_sl['is_post_sl'] else 'Fresh signal'
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
    print(f'ADM: {result["adm_phase"]} sweep={result["adm_sweep"]}')
    print(f'OB near zone: {result["ob_near_zone"]}')
    print(f'High confluences: {result["high_confluences"]}')
    print('Components:')
    for k, v in result['components'].items():
        print(f'  {k}: {v}')
    print('Details:')
    for k, v in result['details'].items():
        print(f'  {k}: {v}')
