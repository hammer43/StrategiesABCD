import pickle
import numpy as np
from datetime import datetime, timezone
import sys
sys.path.insert(0, '/root/gold-signals/system')

MODEL_FILE = '/root/gold-signals/system/model.pkl'

with open(MODEL_FILE, 'rb') as f:
    model = pickle.load(f)

def score_signal(signal, price_data=None):
    dt      = datetime.now(timezone.utc)
    hour    = dt.hour
    day     = dt.weekday()

    zone_width = round(signal['zone_top'] - signal['zone_bot'], 2)
    tp1_dist   = round(signal['tp1'] - signal['zone_top'], 2)
    sl_dist    = round(signal['zone_bot'] - signal['sl'], 2)
    rr         = round((signal['tp1'] - signal['zone_bot']) / sl_dist, 2) if sl_dist > 0 else 0
    high_risk  = 1 if signal.get('high_risk') else 0
    session    = 0 if hour < 7 else (1 if hour < 13 else (2 if hour < 22 else 3))

    features = np.array([[hour, day, zone_width, tp1_dist, sl_dist, rr, high_risk, session]])
    prob = model.predict_proba(features)[0][1]
    confidence = round(prob * 100, 1)

    if confidence >= 80:
        tier   = 'high'
        action = 'TAKE_HIGH'
    elif confidence >= 70:
        tier   = 'mid'
        action = 'TAKE_MID'
    elif confidence >= 55:
        tier   = 'low'
        action = 'TAKE_LOW'
    else:
        tier   = 'skip'
        action = 'SKIP'

    return {
        'confidence': confidence,
        'tier':       tier,
        'action':     action,
        'features': {
            'hour': hour, 'day': day,
            'zone_width': zone_width,
            'tp1_dist': tp1_dist,
            'rr': rr,
            'session': session
        }
    }

if __name__ == '__main__':
    test = {
        'zone_top': 4725.0, 'zone_bot': 4720.0,
        'tp1': 4729.0, 'sl': 4719.0, 'high_risk': False
    }
    result = score_signal(test)
    print(f'Confidence: {result["confidence"]}%')
    print(f'Tier: {result["tier"]}')
    print(f'Action: {result["action"]}')
