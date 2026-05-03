import pandas as pd
from datetime import datetime
from typing import Dict, Optional

class FeatureEngineer:
    def create_features(self, df_1m, df_5m, df_15m, current_price, timestamp, fvg=None, mitigation_level=None, htf_bias="bullish", recent_sweep=None):
        features = {}
        features['hour'] = timestamp.hour
        features['day_of_week'] = timestamp.weekday()
        features['is_london_killzone'] = 1 if 5 <= timestamp.hour <= 8 else 0
        features['is_ny_overlap'] = 1 if 12 <= timestamp.hour <= 16 else 0
        if fvg and mitigation_level is not None:
            fvg_width = fvg.get('top', 0) - fvg.get('bottom', 0)
            fvg_mid = (fvg.get('top', 0) + fvg.get('bottom', 0)) / 2
            features['fvg_width'] = fvg_width
            features['fvg_mid'] = fvg_mid
            features['price_to_fvg_mid'] = abs(current_price - fvg_mid)
            features['price_to_50pct_mitigation'] = abs(current_price - mitigation_level)
            features['fvg_relative_position'] = (current_price - fvg.get('bottom', 0)) / fvg_width if fvg_width > 0 else 0
            features['fvg_age'] = fvg.get('age', 0)
            features['fvg_direction'] = 1 if fvg.get('bullish', True) else -1
        else:
            features.update({'fvg_width': 0, 'price_to_50pct_mitigation': 999, 'fvg_relative_position': 0, 'fvg_age': 0})
        if recent_sweep:
            features['sweep_depth'] = recent_sweep.get('depth', 0)
            features['sweep_wick_ratio'] = recent_sweep.get('wick_ratio', 0)
            features['sweep_body_ratio'] = recent_sweep.get('body_ratio', 0)
            features['sweep_strength'] = recent_sweep.get('depth', 0) * recent_sweep.get('wick_ratio', 0)
        else:
            features.update({'sweep_depth': 0, 'sweep_wick_ratio': 0, 'sweep_body_ratio': 0, 'sweep_strength': 0})
        features['momentum_1m_5'] = self._momentum(df_1m, 5)
        features['momentum_5m_3'] = self._momentum(df_5m, 3)
        features['momentum_15m_2'] = self._momentum(df_15m, 2)
        features['atr_5m'] = self._get_atr(df_5m, 14)
        features['rsi_5m'] = self._rsi(df_5m, 14)
        features['dist_to_session_low'] = current_price - df_15m['low'].rolling(20).min().iloc[-1]
        features['dist_to_session_high'] = df_15m['high'].rolling(20).max().iloc[-1] - current_price
        features['htf_bias'] = 1 if htf_bias == "bullish" else 0
        est_sl = mitigation_level - 10 if mitigation_level else current_price - 12
        features['risk_distance'] = abs(current_price - est_sl)
        features['rr_potential_tp1'] = 35 / features['risk_distance'] if features['risk_distance'] > 0 else 0
        features['rr_potential_tp3'] = 130 / features['risk_distance'] if features['risk_distance'] > 0 else 0
        features['volume_spike_5m'] = self._volume_spike(df_5m)
        return features

    def _momentum(self, df, periods):
        if len(df) < periods + 1:
            return 0.0
        return float(df['close'].iloc[-1] - df['close'].iloc[-periods])

    def _get_atr(self, df, period=14):
        if len(df) < period:
            return 10.0
        tr = pd.concat([df['high']-df['low'], abs(df['high']-df['close'].shift()), abs(df['low']-df['close'].shift())], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def _rsi(self, df, period=14):
        if len(df) < period + 1:
            return 50.0
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return float(100 - (100 / (1 + rs.iloc[-1])))

    def _volume_spike(self, df, lookback=20):
        if len(df) < lookback or 'volume' not in df.columns:
            return 1.0
        avg_vol = df['volume'].rolling(lookback).mean().iloc[-1]
        return float(df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 1.0)
