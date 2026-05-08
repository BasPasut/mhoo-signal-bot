"""
XGBoost ML model for LONG/SHORT prediction.
Features are engineered from raw OHLCV data.
Model is trained on first run using recent Binance data,
then retrained weekly.
"""
import numpy as np
import pandas as pd
import ta
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
MODEL_PATH = Path("./ml_model.joblib")

try:
    from xgboost import XGBClassifier
    import joblib
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    logger.warning("XGBoost not available — ML layer will return 0.0")


def _make_features(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    c = df["close"]

    # Price-derived
    f["ret_1"] = c.pct_change(1)
    f["ret_3"] = c.pct_change(3)
    f["ret_6"] = c.pct_change(6)
    f["ret_12"] = c.pct_change(12)

    # RSI
    f["rsi"] = ta.momentum.RSIIndicator(c, 14).rsi()
    f["rsi_slope"] = f["rsi"].diff(3)

    # MACD
    macd = ta.trend.MACD(c)
    f["macd_hist"] = macd.macd_diff()
    f["macd_hist_slope"] = f["macd_hist"].diff(3)

    # Bollinger
    bb = ta.volatility.BollingerBands(c, 20, 2)
    bbu, bbl = bb.bollinger_hband(), bb.bollinger_lband()
    f["bb_pct"] = (c - bbl) / (bbu - bbl + 1e-10)
    f["bb_width"] = bb.bollinger_wband()

    # EMA gaps
    for p in [9, 21, 50]:
        ema = ta.trend.EMAIndicator(c, p).ema_indicator()
        f[f"ema{p}_gap"] = (c - ema) / (ema + 1e-10)

    # Volume
    avg_vol = df["volume"].rolling(20).mean()
    f["vol_ratio"] = df["volume"] / (avg_vol + 1e-10)

    # ATR normalised
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], c, 14).average_true_range()
    f["atr_norm"] = atr / (c + 1e-10)

    # ADX
    adx = ta.trend.ADXIndicator(df["high"], df["low"], c, 14)
    f["adx"] = adx.adx()
    f["adx_pos"] = adx.adx_pos()
    f["adx_neg"] = adx.adx_neg()

    return f


def _make_labels(df: pd.DataFrame, horizon: int = 6, threshold: float = 0.005) -> pd.Series:
    """1 = price goes up ≥ threshold in next horizon bars, 0 = down."""
    future_ret = df["close"].shift(-horizon) / df["close"] - 1
    return (future_ret >= threshold).astype(int)


def train(df: pd.DataFrame) -> bool:
    if not XGB_AVAILABLE or len(df) < 300:
        return False
    try:
        feats = _make_features(df)
        labels = _make_labels(df)
        combined = feats.join(labels.rename("target")).dropna()
        if len(combined) < 200:
            return False
        X = combined.drop("target", axis=1).values
        y = combined["target"].values
        model = XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            verbosity=0,
        )
        model.fit(X, y)
        joblib.dump({"model": model, "features": list(feats.columns)}, MODEL_PATH)
        logger.info(f"ML model trained on {len(X)} samples")
        return True
    except Exception as e:
        logger.error(f"Training failed: {e}")
        return False


def predict(df: pd.DataFrame) -> float:
    """
    Returns a score in [-1, 1]:
      positive = model thinks LONG is likely
      negative = model thinks SHORT is likely
      magnitude = confidence
    """
    if not XGB_AVAILABLE or not MODEL_PATH.exists():
        return 0.0
    try:
        saved = joblib.load(MODEL_PATH)
        model: XGBClassifier = saved["model"]
        feat_cols: list = saved["features"]

        feats = _make_features(df)
        row = feats[feat_cols].dropna().iloc[-1:]
        if row.empty:
            return 0.0

        prob_long = float(model.predict_proba(row)[0][1])
        # Map [0,1] → [-1,1], centred on 0.5
        score = (prob_long - 0.5) * 2
        return max(-1.0, min(1.0, score))
    except Exception as e:
        logger.warning(f"Prediction failed: {e}")
        return 0.0


def needs_training() -> bool:
    if not MODEL_PATH.exists():
        return True
    age = os.path.getmtime(MODEL_PATH)
    import time
    return (time.time() - age) > 7 * 86400  # retrain weekly
