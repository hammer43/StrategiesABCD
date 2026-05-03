import json, re

d = json.load(open('/root/gold-signals/signals.json'))
d_sorted = sorted(d, key=lambda x: x['date'])

SPREAD = 0.2  # BlackBull spread in price units = 2 pips

tp1_distances = []
sl_distances = []
zone_widths = []

for m in d_sorted:
    if not m['text']:
        continue
    upper = m['text'].upper()
    if not (('BUY GOLD' in upper or 'BUY XAUUSD' in upper) and 'SL' in upper and 'TP' in upper):
        continue

    zone = re.search(r'(\d{4}\.?\d*)/(\d{4}\.?\d*)', m['text'])
    tps  = re.findall(r'TP\s+(\d{4}\.?\d*)', m['text'])
    sl   = re.search(r'SL\s+(\d{4}\.?\d*)', m['text'])

    if zone and tps and sl:
        zone_top = max(float(zone.group(1)), float(zone.group(2)))
        zone_bot = min(float(zone.group(1)), float(zone.group(2)))
        tp1      = float(tps[0])
        sl_val   = float(sl.group(1))

        # Our entry at zone_top - 0.1
        entry = zone_top - 0.1

        # Actual fill price including spread
        actual_fill = entry + SPREAD  # ask price

        # TP1 adjusted for spread (we close at bid, so no adjustment needed on exit)
        tp1_gross_pips = round((tp1 - actual_fill) * 10, 1)

        # SL adjusted (spread works against us)
        sl_pips = round((actual_fill - sl_val) * 10, 1)

        # RR
        rr = round(tp1_gross_pips / sl_pips, 2) if sl_pips > 0 else 0

        # Profit at TP1 at 0.05 lots
        tp1_profit = round(tp1_gross_pips * 0.05 * 10 * 0.33, 2)

        if tp1_gross_pips > 0 and sl_pips > 0:
            tp1_distances.append(tp1_gross_pips)
            sl_distances.append(sl_pips)
            zone_widths.append(round((zone_top - zone_bot) * 10, 1))

avg = lambda x: round(sum(x)/len(x), 1) if x else 0

print('=== SPREAD IMPACT ANALYSIS ===')
print(f'Entry: zone_top - 0.1 (bid)')
print(f'Actual fill: entry + {SPREAD} spread (ask)')
print(f'Signals analyzed: {len(tp1_distances)}')
print()
print(f'Avg zone width: {avg(zone_widths)} pips')
print(f'Avg TP1 distance (after spread): {avg(tp1_distances)} pips')
print(f'Avg SL distance (after spread): {avg(sl_distances)} pips')
print(f'Avg RR: {round(avg(tp1_distances)/avg(sl_distances),2) if avg(sl_distances) > 0 else 0}')
print()

# How many trades are profitable after spread?
profitable = len([t for t in tp1_distances if t > 0])
breakeven  = len([t for t in tp1_distances if t == 0])
losing     = len([t for t in tp1_distances if t < 0])
print(f'Profitable after spread: {profitable} ({round(profitable/len(tp1_distances)*100,1)}%)')
print(f'Breakeven after spread: {breakeven}')
print(f'Losing after spread: {losing}')
print()

# Minimum pip requirement to cover spread
print(f'=== MINIMUM PROFITABLE TRADE ===')
print(f'Spread cost: {SPREAD*10} pips')
print(f'To be profitable TP1 must be > {SPREAD*10} pips from entry')
print()

# TP subtraction strategy
print('=== TP ADJUSTMENT STRATEGY ===')
print(f'Subtract {SPREAD} from all TPs to guarantee fills:')
print(f'  TP1: signal_tp1 - {SPREAD}')
print(f'  TP2: signal_tp2 - {SPREAD}')
print(f'  TP3: signal_tp3 - {SPREAD}')
print(f'Add {SPREAD} to SL for buffer:')
print(f'  SL: signal_sl - {SPREAD}')
print()

# Profit at different lot sizes
print('=== PROFIT AT 0.05 LOTS ===')
avg_tp1_pips = avg(tp1_distances)
avg_sl_pips  = avg(sl_distances)
for pct in [0.33, 0.50, 1.0]:
    profit = round(avg_tp1_pips * 0.05 * 10 * pct, 2)
    print(f'  Close {int(pct*100)}% at TP1: +${profit}')
loss = round(avg_sl_pips * 0.05 * 10, 2)
print(f'  Full SL loss: -${loss}')
print(f'  Net per trade (33% TP1, 33% TP2, 34% TP3):')
tp2_profit = round(avg([t*1.5 for t in tp1_distances]) * 0.05 * 10 * 0.33, 2)
tp3_profit = round(avg([t*2.5 for t in tp1_distances]) * 0.05 * 10 * 0.34, 2)
net = round(tp1_profit + tp2_profit + tp3_profit - loss * 0.082, 2)
print(f'  ~${net} per trade')
