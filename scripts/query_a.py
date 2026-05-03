import json
s = json.load(open('/root/gold-signals/system/state.json'))
t = json.load(open('/root/gold-signals/system/trades.json'))
tp1_hit = [x for x in t if x.get('tp1_hit')]
sl_hit  = [x for x in t if x.get('status')=='loss']
be      = [x for x in t if x.get('status')=='breakeven']
closed  = [x for x in t if x.get('status')=='closed']
print(f'Balance: ${round(s["balance"],2)}')
print(f'Profit:  ${round(s["balance"]-5000,2)}')
print(f'Return:  {round((s["balance"]-5000)/5000*100,1)}%')
print(f'Trades:  {len(t)}')
print(f'TP1 hit: {len(tp1_hit)} ({round(len(tp1_hit)/len(t)*100,1)}%)')
print(f'  Closed fully: {len(closed)}')
print(f'  Went to BE:   {len(be)} (old system)')
print(f'SL hit:  {len(sl_hit)} ({round(len(sl_hit)/len(t)*100,1)}%)')
print(f'W/L: {s["total_wins"]}/{s["total_losses"]}')
