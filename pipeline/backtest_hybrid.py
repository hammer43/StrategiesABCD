import json
import re
import numpy as np
import pickle
from datetime import datetime, timezone
from collections import defaultdict

# Load data
signals_raw = json.load(open('/root/gold-signals/signals.json'))
signals_raw = sorted(signals_raw, key=lambda x: x['date'])

with open('/root/gold-signals/system/model.pkl', 'rb') as f:
    old_model = pickle.load(f)

# ── PARSE SIGNALS ─────────────────────────────────────────
def parse_signal(m):
    text = m['text']
    if not text:
        return None
    upper = text.upper()
    if not (('BUY GOLD' in upper or 'BUY XAUUSD' in upper) and 'SL' in upper and 'TP' in upper):
        return None
    zone = re.search(r'(\d{4}\.?\d*)/(\d{4}\.?\d*)', text)
    tps  = re.findall(r'TP\s+(\d{4}\.?\d*)', text)
    sl   = re.search(r'SL\s+(\d{4}\.?\d*)', text)
    if not (zone and tps and sl):
        return None
    zt = float(zone.group(1))
    zb = float(zone.group(2))
    zone_top = max(zt, zb)
    zone_bot = min(zt, zb)
    tp_levels = [float(t) for t in tps]
    sl_val = float(sl.group(1))
    dt = datetime.fromisoformat(m['date'])
    return {
        'date': m['date'],
        'dt': dt,
        'zone_top': zone_top,
        'zone_bot': zone_bot,
        'zone_width': zone_top - zone_bot,
        'zone_70pct': round(zone_bot + (zone_top-zone_bot)*0.7, 2),
        'tp1': tp_levels[0],
        'tp2': tp_levels[1] if len(tp_levels)>1 else None,
        'tp3': tp_levels[2] if len(tp_levels)>2 else None,
        'sl': sl_val,
        'high_risk': 'HIGH RISK' in upper,
        'hour': dt.hour,
        'day': dt.weekday()
    }

# ── FIND OUTCOMES ─────────────────────────────────────────
def find_outcome(idx, all_msgs):
    for msg in all_msgs[idx+1:idx+200]:
        if not msg['text']:
            continue
        txt = msg['text'].upper()
        if 'TP1' in txt and 'HIT' in txt:
            return 'tp1'
        if 'TP2' in txt and 'HIT' in txt:
            return 'tp2'
        if 'TP3' in txt and 'HIT' in txt:
            return 'tp3'
        if 'SL' in txt and 'HIT' in txt:
            return 'sl'
    return None

# ── SIMULATE SWEEP & FVG ──────────────────────────────────
# Since we don't have historical candles, we simulate:
# Sweep probability based on hour and zone position
# FVG probability based on signal characteristics
def simulate_market_context(signal, prev_signals):
    hour = signal['hour']
    
    # Simulate sweep based on session
    # London open and NY overlap have more sweeps
    sweep_prob = 0.0
    if 5 <= hour <= 8:
        sweep_prob = 0.65   # London — frequent sweeps
    elif 12 <= hour <= 16:
        sweep_prob = 0.55   # NY overlap
    elif 19 <= hour <= 21:
        sweep_prob = 0.45   # Asian open
    else:
        sweep_prob = 0.30

    # Re-entry signals more likely have sweep (zone already tested)
    is_reentry = False
    for prev in prev_signals[-5:]:
        if abs(prev['zone_top'] - signal['zone_top']) < 5:
            is_reentry = True
            sweep_prob += 0.15
            break

    sweep_detected = np.random.random() < sweep_prob

    # Simulate FVG — most signals have FVG (it's his core setup)
    fvg_prob = 0.75
    if signal['high_risk']:
        fvg_prob = 0.65   # High risk = less clean FVG
    fvg_detected = np.random.random() < fvg_prob

    # Simulate HTF bias — Gold was mostly bullish Nov25-Apr26
    # But had bearish periods (corrections)
    month = signal['dt'].month
    if month in [11, 12, 1, 2]:
        bull_prob = 0.80   # Strong bull run
    elif month in [3, 4]:
        bull_prob = 0.65   # More volatile
    else:
        bull_prob = 0.75

    r = np.random.random()
    if r < bull_prob:
        htf_bias = 'bullish'
    elif r < bull_prob + 0.15:
        htf_bias = 'ranging'
    else:
        htf_bias = 'bearish'

    # Simulate sweep quality
    sweep = None
    if sweep_detected:
        sweep = {
            'depth': np.random.uniform(2, 15),
            'wick_ratio': np.random.uniform(0.3, 0.8),
            'body_ratio': np.random.uniform(0.2, 0.6),
        }
        sweep['sweep_strength'] = sweep['depth'] * sweep['wick_ratio']

    # Simulate FVG quality
    fvg = None
    if fvg_detected:
        fvg_width = np.random.uniform(2, 8)
        price_to_mid = np.random.uniform(0, 10)
        fvg = {
            'top': signal['zone_top'] + fvg_width/2,
            'bottom': signal['zone_top'] - fvg_width/2,
            'mid': signal['zone_top'],
            'bullish': True,
            'age': np.random.randint(1, 10),
            'price_to_50pct': price_to_mid
        }

    return sweep, fvg, htf_bias, is_reentry

# ── HYBRID SCORE ──────────────────────────────────────────
def compute_hybrid_score(signal, sweep, fvg, htf_bias):
    hour = signal['hour']
    day  = signal['day']
    zw   = signal['zone_width']
    tp1d = signal['tp1'] - signal['zone_top']
    sld  = signal['zone_bot'] - signal['sl']
    rr   = round(tp1d/sld, 2) if sld > 0 else 0
    hr   = 1 if signal['high_risk'] else 0
    sess = 0 if hour<7 else (1 if hour<13 else (2 if hour<22 else 3))

    old_feat = np.array([[hour, day, zw, tp1d, sld, rr, hr, sess]])
    old_prob = old_model.predict_proba(old_feat)[0][1]

    # Hard gates
    if htf_bias == 'bearish' and not sweep:
        return 0.0, 'hard_skip'
    if not fvg and not sweep:
        return 0.0, 'hard_skip'

    # Component scores
    sweep_score    = min(1.0, sweep['sweep_strength']/5) if sweep else 0.0
    fvg_score      = max(0, 1 - fvg.get('price_to_50pct',10)/20) if fvg else 0.0
    session_score  = 1.0 if hour in [5,6,7,8,9,10,11,19,20,21] else 0.6
    momentum_score = 0.8   # simulated positive
    bias_score     = 1.0 if htf_bias=='bullish' else (0.7 if htf_bias=='ranging' else 0.2)

    rule_score = (
        0.35 * sweep_score +
        0.25 * fvg_score +
        0.20 * session_score +
        0.10 * momentum_score +
        0.10 * bias_score
    )

    final = (old_prob * 0.4) + (rule_score * 0.6)
    return round(final * 100, 1), 'scored'

# ── RUN BACKTEST ──────────────────────────────────────────
np.random.seed(42)

parsed = []
for i, m in enumerate(signals_raw):
    s = parse_signal(m)
    if s:
        outcome = find_outcome(i, signals_raw)
        if outcome:
            s['outcome'] = outcome
            s['win'] = outcome != 'sl'
            s['idx'] = i
            parsed.append(s)

print(f'Total labelled signals: {len(parsed)}')
print(f'Base win rate: {round(sum(1 for s in parsed if s["win"])/len(parsed)*100,1)}%')
print()

# Run hybrid scoring
results = []
prev_signals = []

for s in parsed:
    sweep, fvg, htf_bias, is_reentry = simulate_market_context(s, prev_signals)
    confidence, status = compute_hybrid_score(s, sweep, fvg, htf_bias)

    if status == 'hard_skip':
        tier = 'skip'
    elif confidence >= 80:
        tier = 'high'
    elif confidence >= 70:
        tier = 'mid'
    elif confidence >= 60:
        tier = 'low'
    else:
        tier = 'skip'

    results.append({
        'win': s['win'],
        'confidence': confidence,
        'tier': tier,
        'hour': s['hour'],
        'htf_bias': htf_bias,
        'sweep': sweep is not None,
        'fvg': fvg is not None,
        'is_reentry': is_reentry
    })
    prev_signals.append(s)

# ── RESULTS ───────────────────────────────────────────────
print('=== HYBRID BACKTEST RESULTS ===')
print()

for tier in ['high', 'mid', 'low', 'skip']:
    group = [r for r in results if r['tier'] == tier]
    if not group:
        continue
    wins = sum(1 for r in group if r['win'])
    total = len(group)
    wr = round(wins/total*100, 1)
    pct = round(total/len(results)*100, 1)
    print(f'{tier.upper():6} | signals={total:4d} ({pct:5.1f}%) | WR={wr:5.1f}%')

print()
tradeable = [r for r in results if r['tier'] != 'skip']
skipped   = [r for r in results if r['tier'] == 'skip']
t_wins    = sum(1 for r in tradeable if r['win'])
s_wins    = sum(1 for r in skipped if r['win'])

print(f'Tradeable: {len(tradeable)} signals')
print(f'Tradeable WR: {round(t_wins/len(tradeable)*100,1)}% vs base {round(sum(1 for s in parsed if s["win"])/len(parsed)*100,1)}%')
print(f'Skipped: {len(skipped)} ({round(len(skipped)/len(results)*100,1)}%)')
print(f'Skipped WR would have been: {round(s_wins/len(skipped)*100,1)}% (good to skip if low)')

print()
print('=== BY HTF BIAS ===')
for bias in ['bullish', 'ranging', 'bearish']:
    group = [r for r in results if r['htf_bias'] == bias]
    if not group:
        continue
    wins = sum(1 for r in group if r['win'])
    wr = round(wins/len(group)*100,1)
    print(f'{bias:8}: {len(group):4d} signals WR={wr}%')

print()
print('=== SWEEP vs NO SWEEP ===')
for has_sweep in [True, False]:
    group = [r for r in results if r['sweep'] == has_sweep and r['tier'] != 'skip']
    if not group:
        continue
    wins = sum(1 for r in group if r['win'])
    wr = round(wins/len(group)*100,1)
    label = 'Sweep' if has_sweep else 'No sweep'
    print(f'{label:10}: {len(group):4d} signals WR={wr}%')

print()
print('=== RE-ENTRY vs FRESH ===')
for is_re in [True, False]:
    group = [r for r in results if r['is_reentry'] == is_re and r['tier'] != 'skip']
    if not group:
        continue
    wins = sum(1 for r in group if r['win'])
    wr = round(wins/len(group)*100,1)
    label = 'Re-entry' if is_re else 'Fresh'
    print(f'{label:10}: {len(group):4d} signals WR={wr}%')

print()
print('=== WEEKLY TRADE ESTIMATE ===')
weekly_signals = len(parsed) / 24  # 6 months = ~24 weeks
weekly_tradeable = len(tradeable) / 24
high_trades = len([r for r in results if r['tier']=='high']) / 24
mid_trades  = len([r for r in results if r['tier']=='mid']) / 24
low_trades  = len([r for r in results if r['tier']=='low']) / 24

print(f'Avg signals/week: {round(weekly_signals,1)}')
print(f'Avg tradeable/week: {round(weekly_tradeable,1)}')
print(f'  High tier: {round(high_trades,1)}/week (2 layers)')
print(f'  Mid tier:  {round(mid_trades,1)}/week (2 layers)')
print(f'  Low tier:  {round(low_trades,1)}/week (1 layer)')

# P&L estimate
pip_value = 0.50  # $0.50/pip at 0.05 lots
avg_tp1 = 66.4
avg_sl  = 10.0

high_wr = round(sum(1 for r in results if r['tier']=='high' and r['win'])/max(1,len([r for r in results if r['tier']=='high']))*100,1)
mid_wr  = round(sum(1 for r in results if r['tier']=='mid' and r['win'])/max(1,len([r for r in results if r['tier']=='mid']))*100,1)
low_wr  = round(sum(1 for r in results if r['tier']=='low' and r['win'])/max(1,len([r for r in results if r['tier']=='low']))*100,1)

def weekly_pnl(trades_per_week, wr, layers=2):
    wins   = trades_per_week * layers * (wr/100)
    losses = trades_per_week * layers * (1-wr/100)
    return round(wins * avg_tp1 * pip_value * 0.33 - losses * avg_sl * pip_value, 2)

high_pnl = weekly_pnl(high_trades, high_wr, 2)
mid_pnl  = weekly_pnl(mid_trades,  mid_wr,  2)
low_pnl  = weekly_pnl(low_trades,  low_wr,  1)
total_pnl = high_pnl + mid_pnl + low_pnl

print()
print('=== PROJECTED WEEKLY P&L ===')
print(f'High tier ({high_wr}% WR): ${high_pnl}/week')
print(f'Mid tier  ({mid_wr}% WR):  ${mid_pnl}/week')
print(f'Low tier  ({low_wr}% WR):  ${low_pnl}/week')
print(f'Total weekly: ${total_pnl}')
print(f'Monthly: ${round(total_pnl*4,2)}')
print(f'Return on $5000: {round(total_pnl*4/5000*100,1)}%/month')
