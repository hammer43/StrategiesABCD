import json, re
from datetime import datetime

d = json.load(open('/root/gold-signals/signals.json'))
d_sorted = sorted(d, key=lambda x: x['date'])

pullbacks = []
no_pullback = []

for m in d_sorted:
    if not m['text']:
        continue
    text = m['text']
    upper = text.upper()
    
    # Messages that mention pullback before TP
    # "TP1 HIT +30 PIPS AFTER PULLING BACK TO 4695"
    # "TP1 HIT +40 PIPS AGAIN AFTER PULLING BACK TO 4695.7"
    if ('TP1 HIT' in upper or 'AT TP1' in upper) and 'PULLING BACK TO' in upper:
        pips = re.search(r'\+(\d+)\s*PIPS?', upper)
        pullback_price = re.search(r'PULLING BACK TO\s*([\d.]+)', upper)
        if pips and pullback_price:
            pullbacks.append({
                'pips': int(pips.group(1)),
                'pullback_price': float(pullback_price.group(1)),
                'text': text[:100]
            })
    
    # Messages where TP hit without pullback mention
    elif 'TP1 HIT' in upper and 'PIPS' in upper and 'PULLING' not in upper:
        pips = re.search(r'\+(\d+)\s*PIPS?', upper)
        if pips:
            no_pullback.append(int(pips.group(1)))

print(f'TP1 hits WITH pullback mention: {len(pullbacks)}')
print(f'TP1 hits WITHOUT pullback mention: {len(no_pullback)}')
print()

if pullbacks:
    avg_pips = round(sum(p["pips"] for p in pullbacks)/len(pullbacks),1)
    print(f'Avg pips at TP1 (with pullback): {avg_pips}')
    print()
    print('Sample pullback messages:')
    for p in pullbacks[:10]:
        print(f'  +{p["pips"]} pips, pulled back to {p["pullback_price"]}')
        print(f'  {p["text"][:80]}')
        print()
