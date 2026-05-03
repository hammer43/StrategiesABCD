import pickle
import numpy as np
from datetime import datetime, timezone

MODEL_FILE = '/root/gold-signals/system/model.pkl'

with open(MODEL_FILE, 'rb') as f:
    model = pickle.load(f)

def score_signal(signal):
    dt = datetime.now(timezone.utc)
    hour = dt.hour
    day = dt.weekday()
    zone_width = round(signal['zone_top'] - signal['zone_bot'], 2)
    tp1_dist = round(signal['tp1'] - signal['zone_top'], 2)
    sl_dist = round(signal['zone_bot'] - signal['sl'], 2)
    rr = round((signal['tp1'] - signal['zone_bot']) / sl_dist, 2) if sl_dist > 0 else 0
    high_risk = 1 if signal.get('high_risk') else 0
    session = 0 if hour < 7 else (1 if hour < 13 else (2 if hour < 22 else 3))

    features = np.array([[hour, day, zone_width, tp1_dist, sl_dist, rr, high_risk, session]])
    prob = model.predict_proba(features)[0][1]
    confidence = round(prob * 100, 1)

    if confidence >= 80:
        tier = 'high'
        action = 'TAKE_HIGH'
    elif confidence >= 70:
        tier = 'mid'
        action = 'TAKE_MID'
    elif confidence >= 40:
        tier = 'low'
        action = 'TAKE_LOW'
    else:
        tier = 'skip'
        action = 'SKIP'

    return {
        'confidence': confidence,
        'tier': tier,
        'action': action
    }
