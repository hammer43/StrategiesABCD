import json, re

d = json.load(open('/root/gold-signals/signals.json'))
d_sorted = sorted(d, key=lambda x: x['date'])

pullback_depths = []
no_pullback_count = 0
hung_around_count = 0

for i, m in enumerate(d_sorted):
    if not m['text']:
        continue
    text = m['text']
    upper = text.upper()

    if ('TP1 HIT' in upper or 'AT TP1' in upper) and 'PIPS' in upper:
        
        if 'PULLING BACK TO' in upper:
            pips = re.search(r'\+(\d+)\s*PIPS?', upper)
            pullback = re.search(r'PULLING BACK TO\s*([\d.]+)', upper)
            if pips and pullback:
                tp1_pips = int(pips.group(1))
                pb_price = float(pullback.group(1))
                # Pullback depth = TP1 pips - distance pulled back
                # We need entry price to calculate exactly
                # Approximate: if TP1 = +30 pips and pulled back,
                # pullback was likely 10-20 pips from entry
                pullback_depths.append({
                    'tp1_pips': tp1_pips,
                    'pullback_price': pb_price
                })
        
        elif 'HUNG AROUND' in upper or 'PERFECTLY' in upper:
            hung_around_count += 1
        
        else:
            no_pullback_count += 1

total = len(pullback_depths) + no_pullback_count + hung_around_count
print(f'Total TP1 hits analyzed: {total}')
print(f'With pullback: {len(pullback_depths)} ({round(len(pullback_depths)/total*100,1)}%)')
print(f'Hung around zone (no pullback): {hung_around_count} ({round(hung_around_count/total*100,1)}%)')
print(f'Straight to TP1: {no_pullback_count} ({round(no_pullback_count/total*100,1)}%)')
print()

if pullback_depths:
    avg_tp1 = round(sum(p['tp1_pips'] for p in pullback_depths)/len(pullback_depths),1)
    print(f'Avg TP1 pips on pullback trades: {avg_tp1}')

# Now cross-reference with signals to get pullback depth
print()
print('=== PULLBACK DEPTH ESTIMATION ===')
print('Looking for signals followed by pullback TP1 messages...')

matched = []
for i, m in enumerate(d_sorted):
    if not m['text']:
        continue
    upper = m['text'].upper()
    if not (('BUY GOLD' in upper or 'BUY XAUUSD' in upper) and 'SL' in upper and 'TP' in upper):
        continue
    
    zone = re.search(r'(\d{4}\.?\d*)/(\d{4}\.?\d*)', m['text'])
    if not zone:
        continue
    
    zone_top = max(float(zone.group(1)), float(zone.group(2)))
    zone_bot = min(float(zone.group(1)), float(zone.group(2)))
    
    for msg in d_sorted[i+1:i+30]:
        if not msg['text']:
            continue
        msg_upper = msg['text'].upper()
        if 'PULLING BACK TO' in msg_upper and ('TP1 HIT' in msg_upper or 'AT TP1' in msg_upper):
            pips = re.search(r'\+(\d+)\s*PIPS?', msg_upper)
            pullback = re.search(r'PULLING BACK TO\s*([\d.]+)', msg_upper)
            if pips and pullback:
                pb_price = float(pullback.group(1))
                tp1_pips = int(pips.group(1))
                # How deep did it pull back from zone top?
                pullback_from_zone = round((zone_top - pb_price) * 10, 1)
                if -50 < pullback_from_zone < 200:
                    matched.append({
                        'zone_top': zone_top,
                        'zone_bot': zone_bot,
                        'pullback_price': pb_price,
                        'pullback_from_zone_top': pullback_from_zone,
                        'tp1_pips': tp1_pips
                    })
            break

if matched:
    depths = [m['pullback_from_zone_top'] for m in matched]
    below_zone = [d for d in depths if d > 0]
    above_zone = [d for d in depths if d <= 0]
    
    print(f'Matched signal+pullback pairs: {len(matched)}')
    print(f'Pulled BELOW zone top: {len(below_zone)} ({round(len(below_zone)/len(matched)*100,1)}%)')
    print(f'Stayed ABOVE zone top: {len(above_zone)} ({round(len(above_zone)/len(matched)*100,1)}%)')
    print()
    if below_zone:
        avg_depth = round(sum(below_zone)/len(below_zone),1)
        max_depth = round(max(below_zone),1)
        under10   = len([d for d in below_zone if d <= 10])
        under20   = len([d for d in below_zone if d <= 20])
        under30   = len([d for d in below_zone if d <= 30])
        print(f'Avg pullback depth below zone top: {avg_depth} pips')
        print(f'Max pullback depth: {max_depth} pips')
        print(f'Pullback <= 10 pips: {under10} ({round(under10/len(below_zone)*100,1)}%)')
        print(f'Pullback <= 20 pips: {under20} ({round(under20/len(below_zone)*100,1)}%)')
        print(f'Pullback <= 30 pips: {under30} ({round(under30/len(below_zone)*100,1)}%)')
