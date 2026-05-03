import json, numpy as np, pickle

data = json.load(open('/root/gold-signals/system/labelled_data.json'))
with open('/root/gold-signals/system/model.pkl', 'rb') as f:
    model = pickle.load(f)

X = np.array([[d['hour'], d['day'], d['zone_width'], d['tp1_dist'],
               d['sl_dist'], d['rr'], d['high_risk'], d['session']] for d in data])
y = np.array([d['label'] for d in data])
probs = model.predict_proba(X)[:,1]

for threshold in [0.50, 0.55, 0.60, 0.65, 0.70]:
    group = [(p,l) for p,l in zip(probs,y) if p >= threshold]
    skipped = [(p,l) for p,l in zip(probs,y) if p < threshold]
    wins = sum(1 for p,l in group if l==1)
    total = len(group)
    skip_wr = round(sum(1 for p,l in skipped if l==1)/len(skipped)*100,1) if skipped else 0
    wr = round(wins/total*100,1) if total else 0
    print(f'Threshold {threshold}: trades={total} WR={wr}% skipped={len(skipped)} skipped_WR={skip_wr}%')
