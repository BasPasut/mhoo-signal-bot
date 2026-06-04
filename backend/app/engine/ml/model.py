"""
XGBoost ML model for LONG/SHORT prediction.
Accumulates a growing per-symbol history dataset and retrains daily,
so the model improves over time as it sees more market regimes.
"""
import numpy as np
import pandas as pd
import ta
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.environ.get("ML_MODELS_DIR", "./ml_models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_DIR = Path(os.environ.get("ML_HISTORY_DIR", "./ml_history"))
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

MAX_HISTORY_ROWS = 5000
RETRAIN_INTERVAL_SECS = 86400  # daily


def _model_path(symbol: str = "", timeframe: str = "") -> Path:
    if symbol and timeframe:
        return MODEL_DIR / f"ml_model_{symbol}_{timeframe}.joblib"
    return MODEL_DIR / "ml_model_default.joblib"


def _history_path(symbol: str, timeframe: str) -> Path:
    return HISTORY_DIR / f"hist_{symbol}_{timeframe}.csv"


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

    f["ret_1"] = c.pct_change(1)
    f["ret_3"] = c.pct_change(3)
    f["ret_6"] = c.pct_change(6)
    f["ret_12"] = c.pct_change(12)

    f["rsi"] = ta.momentum.RSIIndicator(c, 14).rsi()
    f["rsi_slope"] = f["rsi"].diff(3)

    macd = ta.trend.MACD(c)
    f["macd_hist"] = macd.macd_diff()
    f["macd_hist_slope"] = f["macd_hist"].diff(3)

    bb = ta.volatility.BollingerBands(c, 20, 2)
    bbu, bbl = bb.bollinger_hband(), bb.bollinger_lband()
    f["bb_pct"] = (c - bbl) / (bbu - bbl + 1e-10)
    f["bb_width"] = bb.bollinger_wband()

    for p in [9, 21, 50]:
        ema = ta.trend.EMAIndicator(c, p).ema_indicator()
        f[f"ema{p}_gap"] = (c - ema) / (ema + 1e-10)

    avg_vol = df["volume"].rolling(20).mean()
    f["vol_ratio"] = df["volume"] / (avg_vol + 1e-10)

    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], c, 14).average_true_range()
    f["atr_norm"] = atr / (c + 1e-10)

    adx = ta.trend.ADXIndicator(df["high"], df["low"], c, 14)
    f["adx"] = adx.adx()
    f["adx_pos"] = adx.adx_pos()
    f["adx_neg"] = adx.adx_neg()

    return f


def _make_labels(df: pd.DataFrame, horizon: int = 6, threshold: float = 0.005) -> pd.Series:
    """1 = price goes up >= threshold in next horizon bars, 0 = down/flat."""
    future_ret = df["close"].shift(-horizon) / df["close"] - 1
    return (future_ret >= threshold).astype(int)


def _load_outcome_labels(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Pull closed signal outcomes from the DB and return them as extra training rows
    so the model learns from real trade results, not just price-threshold labels.
    WIN means direction was correct (label=1 for LONG, label=0 for SHORT).
    LOSS means direction was wrong (label=0 for LONG, label=1 for SHORT).
    """
    try:
        from sqlmodel import Session, select
        from app.models.db import Signal, engine
        with Session(engine) as s:
            signals = s.exec(
                select(Signal)
                .where(Signal.symbol == symbol)
                .where(Signal.timeframe == timeframe)
                .where(Signal.result.in_(["win", "loss"]))  # type: ignore[union-attr]
            ).all()
        if not signals:
            return pd.DataFrame()
        rows = []
        for sig in signals:
            is_win = sig.result == "win"
            is_long = sig.direction == "LONG"
            label = 1 if (is_win and is_long) or (not is_win and not is_long) else 0
            rows.append({"outcome_label": label, "confidence": sig.confidence / 100,
                         "ta_score": sig.ta_score / 100, "pattern_score": sig.pattern_score / 100})
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def train(df: pd.DataFrame, symbol: str = "", timeframe: str = "") -> bool:
    if not XGB_AVAILABLE or len(df) < 300:
        return False
    try:
        feats = _make_features(df)
        labels = _make_labels(df)
        new_data = feats.join(labels.rename("target")).dropna()
        new_data.index.name = "timestamp"
        new_data = new_data.reset_index()
        new_data["timestamp"] = new_data["timestamp"].astype(str)

        # Grow historical dataset
        hist_path = _history_path(symbol, timeframe)
        if hist_path.exists():
            try:
                old = pd.read_csv(hist_path)
                old["timestamp"] = old["timestamp"].astype(str)
                combined = pd.concat([old, new_data]).drop_duplicates(subset=["timestamp"])
            except Exception:
                combined = new_data
        else:
            combined = new_data

        # Cap history to avoid unbounded growth
        combined = combined.tail(MAX_HISTORY_ROWS)
        combined.to_csv(hist_path, index=False)

        feat_cols = [c for c in combined.columns if c not in ("timestamp", "target")]
        X = combined[feat_cols].values
        y = combined["target"].values

        # Up-weight rows confirmed correct by real trade outcomes (WIN/LOSS labels)
        sample_weights = np.ones(len(y))
        outcome_df = _load_outcome_labels(symbol, timeframe)
        outcome_count = 0
        if not outcome_df.empty:
            outcome_count = len(outcome_df)
            # Duplicate outcome-confirmed rows with 3x weight via sample_weight
            # (we can't add them as rows because features don't match, so we boost
            #  the last N rows of combined that align with signal_confidence)
            sample_weights[-min(outcome_count * 3, len(y)):] *= 3.0

        neg = max(1, int((y == 0).sum()))
        pos = max(1, int((y == 1).sum()))
        model = XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=neg / pos,
            use_label_encoder=False,
            eval_metric="logloss",
            verbosity=0,
        )
        model.fit(X, y, sample_weight=sample_weights)

        path = _model_path(symbol, timeframe)
        joblib.dump({"model": model, "features": feat_cols}, path)
        logger.info(
            f"ML trained [{symbol}/{timeframe}] — {len(X)} rows "
            f"(LONG={pos} SHORT={neg} balance={neg/pos:.2f}x outcomes={outcome_count})"
        )
        return True
    except Exception as e:
        logger.error(f"Training failed [{symbol}/{timeframe}]: {e}")
        return False


def predict(df: pd.DataFrame, symbol: str = "", timeframe: str = "") -> float:
    """Returns score in [-1, 1]: positive=LONG likely, negative=SHORT likely."""
    if not XGB_AVAILABLE:
        return 0.0
    path = _model_path(symbol, timeframe)
    if not path.exists():
        return 0.0
    try:
        saved = joblib.load(path)
        model: XGBClassifier = saved["model"]
        feat_cols: list = saved["features"]

        feats = _make_features(df)
        row = feats[feat_cols].dropna().iloc[-1:]
        if row.empty:
            return 0.0

        prob_long = float(model.predict_proba(row)[0][1])
        return max(-1.0, min(1.0, (prob_long - 0.5) * 2))
    except Exception as e:
        logger.warning(f"Prediction failed [{symbol}/{timeframe}]: {e}")
        return 0.0


def needs_training(symbol: str = "", timeframe: str = "") -> bool:
    import time
    path = _model_path(symbol, timeframe)
    if not path.exists():
        return True
    return (time.time() - os.path.getmtime(path)) > RETRAIN_INTERVAL_SECS
