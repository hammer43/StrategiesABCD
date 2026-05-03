import json, re

d = json.load(open('/root/gold-signals/signals.json'))
d_sorted = sorted(d, key=lambda x: x['date'])

runners = []
non_runners = []

for i, m in enumerate(d_sorted):
    if not m['text']:
        continue
    upper = m['text'].upper()
    if not (('BUY GOLD' in upper or 'BUY XAUUSD' in upper) and 'SL' in upper and 'TP' in upper):
        continue
    max_pips = 0
    for msg in d_sorted[i+1:i+100]:
        if not msg['text']:
            continue
        pips = re.search(r'\+(\d+)\s*PIPS?', msg['text'].upper())
        if pips:
            max_pips = max(max_pips, int(pips.group(1)))
    if max_pips >= 300:
        runners.append(max_pips)
    elif max_pips > 0:
        non_runners.append(max_pips)

print(f'Runners (300+ pips): {len(runners)}')
print(f'Non-runners: {len(non_runners)}')
print(f'Runner rate: {round(len(runners)/(len(runners)+len(non_runners))*100,1)}%')
print(f'Avg runner pips: {round(sum(runners)/len(runners),0) if runners else 0}')
print(f'Max runner: {max(runners) if runners else 0}')
