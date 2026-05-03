import json, re
from datetime import datetime, timedelta

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
    
    sig_time = datetime.fromisoformat(m['date'])
    max_pips = 0
    
    for msg in d_sorted[i+1:i+50]:
        if not msg['text']:
            continue
        msg_time = datetime.fromisoformat(msg['date'])
        # Only look within 4 hours
        if (msg_time - sig_time).total_seconds() > 14400:
            break
        # Stop if new signal arrives
        msg_upper = msg['text'].upper()
        if ('BUY GOLD' in msg_upper or 'BUY XAUUSD' in msg_upper) and 'SL' in msg_upper:
            break
        pips = re.search(r'\+(\d+)\s*PIPS?', msg['text'].upper())
        if pips:
            max_pips = max(max_pips, int(pips.group(1)))
    
    if max_pips >= 300:
        runners.append(max_pips)
    elif max_pips > 0:
        non_runners.append(max_pips)
    elif max_pips == 0:
        non_runners.append(0)

total = len(runners) + len(non_runners)
print(f'Total signals analyzed: {total}')
print(f'Runners (300+ pips): {len(runners)} ({round(len(runners)/total*100,1)}%)')
print(f'Non-runners: {len(non_runners)} ({round(len(non_runners)/total*100,1)}%)')
print(f'Avg runner pips: {round(sum(runners)/len(runners),0) if runners else 0}')
print(f'Max runner: {max(runners) if runners else 0}')
print()
print('Runner distribution:')
for threshold in [300, 500, 750, 1000, 1500, 2000]:
    count = len([r for r in runners if r >= threshold])
    print(f'  {threshold}+ pips: {count} ({round(count/total*100,1)}%)')
