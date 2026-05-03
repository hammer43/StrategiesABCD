import pickle
import numpy as np
from datetime import datetime, timezone
from ml.feature_engineer import FeatureEngineer

MODEL_FILE = '/root/gold-signals/system/model.pkl'

with open(MODEL_FILE, 'rb') as f:
    old_model = pickle.load(f)

fe = FeatureEngineer()

def hybrid_score(signal, df_1m, df_5m, df_15m, df_4h,
                 fvg, sweep, mitigation_level, htf_bias):

    current_price = float(df_1m['close'].iloc[-1])
    timestamp = datetime.now(timezone.utc)
    hour = timestamp.hour
    day  = timestamp.weekday()

    # ── OLD ML SCORE ──────────────────────────────────
    zw   = signal['zone_top'] - signal['zone_bot']
    tp1d = signal['tp1'] - signal['zone_top']
    sld  = signal['zone_bot'] - signal['sl']
    rr   = round(tp1d / sld, 2) if sld > 0 else 0
    hr   = 1 if signal.get('high_risk') else 0
    sess = 0 if hour < 7 else (1 if hour < 13 else (2 if hour < 22 else 3))

    old_features = np.array([[hour, day, zw, tp1d, sld, rr, hr, sess]])
    old_prob = old_model.predict_proba(old_features)[0][1]

    # ── RICH FEATURES ────────────────────────────────
    rich = fe.create_features(
        df_1m, df_5m, df_15m,
        current_price, timestamp,
        fvg=fvg,
        mitigation_level=mitigation_level,
        htf_bias=htf_bias,
        recent_sweep=sweep
    )

    # ── COMPONENT SCORES ─────────────────────────────
    sweep_score    = min(1.0, rich['sweep_strength'] / 5) if sweep else 0.0
    fvg_score      = max(0, 1 - rich['price_to_50pct_mitigation'] / 20) if fvg else 0.0
    momentum_score = 1.0 if rich['momentum_5m_3'] > 0 else 0.3
    session_score  = 1.0 if rich['is_london_killzone'] or rich['is_ny_overlap'] else 0.6

    # Bias score — ranging still tradeable for Gold bull market
    if htf_bias == 'bullish':
        bias_score = 1.0
    elif htf_bias == 'ranging':
        bias_score = 0.7   # still tradeable
    else:
        bias_score = 0.2   # bearish — heavily penalised but not hard blocked

    # ── HARD GATES ───────────────────────────────────
    hard_skip = False
    skip_reason = ''

    if htf_bias == 'bearish' and not sweep:
        hard_skip = True
        skip_reason = 'Bearish bias + no sweep'

    if not fvg and not sweep:
        hard_skip = True
        skip_reason = 'No FVG and no sweep'

    # ── RULE SCORE ───────────────────────────────────
    rule_score = (
        0.35 * sweep_score +
        0.25 * fvg_score +
        0.20 * session_score +
        0.10 * momentum_score +
        0.10 * bias_score
    )

    # ── HYBRID CONFIDENCE ────────────────────────────
    if hard_skip:
        final_score = 0.0
    else:
        final_score = (old_prob * 0.4) + (rule_score * 0.6)

    confidence = round(final_score * 100, 1)

    # ── TIER ─────────────────────────────────────────
    if hard_skip or confidence < 55:
        tier = 'skip'; action = 'SKIP'
    elif confidence >= 80:
        tier = 'high'; action = 'TAKE_HIGH'
    elif confidence >= 70:
        tier = 'mid'; action = 'TAKE_MID'
    else:
        tier = 'low'; action = 'TAKE_LOW'

    return {
        'action':     action,
        'tier':       tier,
        'confidence': confidence,
        'skip_reason': skip_reason if hard_skip else '',
        'components': {
            'old_ml':   round(old_prob * 100, 1),
            'sweep':    round(sweep_score * 100, 1),
            'fvg':      round(fvg_score * 100, 1),
            'momentum': round(momentum_score * 100, 1),
            'session':  round(session_score * 100, 1),
            'bias':     round(bias_score * 100, 1),
            'rule':     round(rule_score * 100, 1)
        },
        'market': {
            'htf_bias':   htf_bias,
            'sweep':      sweep is not None,
            'fvg':        fvg is not None,
            'fvg_age':    fvg.get('age', 0) if fvg else 0,
            'mitigation': round(mitigation_level, 2) if mitigation_level else None,
            'rsi':        round(rich['rsi_5m'], 1),
            'momentum_5m': round(rich['momentum_5m_3'], 2),
            'dist_session_low': round(rich['dist_to_session_low'], 1)
        }
    }

if __name__ == '__main__':
    print('Hybrid scorer ready')
