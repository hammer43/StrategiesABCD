import json, re
from datetime import datetime, timedelta

d = json.load(open('/root/gold-signals/signals.json'))
d_sorted = sorted(d, key=lambda x: x['date'])

# For each signal, find the pullback depth from our message data
pullback_data = []

for i, m in enumerate(d_sorted):
    if not m['text']:
        continue
    upper = m['text'].upper()
    if not (('BUY GOLD' in upper or 'BUY XAUUSD' in upper) and 'SL' in upper and 'TP' in upper):
        continue
    
    zone = re.search(r'(\d{4}\.?\d*)/(\d{4}\.?\d*)', m['text'])
    tps  = re.findall(r'TP\s+(\d{4}\.?\d*)', m['text'])
    sl   = re.search(r'SL\s+(\d{4}\.?\d*)', m['text'])
    
    if not (zone and tps and sl):
        continue
    
    zone_top = max(float(zone.group(1)), float(zone.group(2)))
    zone_bot = min(float(zone.group(1)), float(zone.group(2)))
    tp1      = float(tps[0])
    sl_val   = float(sl.group(1))
    
    sig_time = datetime.fromisoformat(m['date'])
    
    # Find outcome and pullback
    outcome = None
    pullback_from_top = 0
    
    for msg in d_sorted[i+1:i+50]:
        if not msg['text']:
            continue
        msg_time = datetime.fromisoformat(msg['date'])
        if (msg_time - sig_time).total_seconds() > 14400:
            break
        msg_upper = msg['text'].upper()
        
        # New signal = stop looking
        if ('BUY GOLD' in msg_upper or 'BUY XAUUSD' in msg_upper) and 'SL' in msg_upper:
            break
        
        # Get pullback level
        if 'PULLING BACK TO' in msg_upper:
            pb = re.search(r'PULLING BACK TO\s*([\d.]+)', msg_upper)
            if pb:
                pb_price = float(pb.group(1))
                pullback_from_top = round((zone_top - pb_price) * 10, 1)
        
        if 'TP1 HIT' in msg_upper or 'AT TP1' in msg_upper:
            outcome = 'win'
            break
        if 'SL HIT' in msg_upper:
            outcome = 'loss'
            break
    
    if outcome:
        pullback_data.append({
            'zone_top': zone_top,
            'zone_bot': zone_bot,
            'tp1': tp1,
            'sl': sl_val,
            'outcome': outcome,
            'pullback_pips': pullback_from_top
        })

print(f'Signals with outcomes: {len(pullback_data)}')
wins   = [p for p in pullback_data if p['outcome'] == 'win']
losses = [p for p in pullback_data if p['outcome'] == 'loss']
print(f'Wins: {len(wins)} Losses: {len(losses)}')
print()

# Simulate fill rates at different entry levels
print('=== FILL RATE vs ENTRY LEVEL ===')
print('Entry offset | Est Fill Rate | Win Rate | Expected Value')
print('-------------|---------------|----------|---------------')

for offset in [0.15, 0, -0.25, -0.50, -0.85, -1.0, -1.5, -2.0]:
    # Fill rate = % of trades where price pulled back enough
    # Approximate from pullback data
    # If offset = -0.85, need pullback of 8.5 pips from zone top
    required_pullback = -offset * 10  # in pips
    
    if required_pullback <= 0:
        # Price at or above zone top - high fill rate
        fill_rate = 0.90
    else:
        # Need price to pull back X pips into zone
        filled = len([p for p in wins if p['pullback_pips'] >= required_pullback])
        total_wins = len(wins)
        fill_rate = round(filled / total_wins, 2) if total_wins > 0 else 0
    
    # RR at this entry (using avg signal values)
    avg_tp1_dist = 30  # avg pips from zone top to TP1
    entry_offset_pips = offset * 10
    tp1_pips = avg_tp1_dist - entry_offset_pips  # adjusted for entry
    avg_sl_dist = 60  # avg pips from zone top to SL
    sl_pips = avg_sl_dist - entry_offset_pips
    rr = round(tp1_pips / sl_pips, 2) if sl_pips > 0 else 0
    
    # Expected value per 100 signals
    win_rate = 0.891
    tp1_profit = round(tp1_pips * 0.05 * 10 * 0.33, 2)
    sl_loss    = round(sl_pips * 0.05 * 10, 2)
    
    trades_taken = 100 * fill_rate
    ev = round((trades_taken * win_rate * tp1_profit) - 
               (trades_taken * (1-win_rate) * sl_loss), 2)
    
    entry_label = f'zone_top {"+" if offset >= 0 else ""}{offset}'
    print(f'{entry_label:13} | {int(fill_rate*100):5}% est      | {win_rate*100:.1f}%    | ${ev}/100 signals')
