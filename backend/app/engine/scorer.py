"""
Signal Scorer
Combines the 4 analysis layers into a final confidence score and signal.

Weights:
  Technical indicators  40%
  Chart patterns        25%
  ML model              25%
  Market context        10%
"""
import numpy as np
import pandas as pd
import logging
from datetime import datetime
from typing import Optional

from app.engine.indicators.technical import analyze as ta_analyze
from app.engine.patterns.chart import analyze as pattern_analyze
from app.engine.ml.model import predict as ml_predict, train as ml_train, needs_training
from app.engine import binance
from app.core.settings import settings

logger = logging.getLogger(__name__)

WEIGHTS = {"ta": 0.40, "pattern": 0.25, "ml": 0.25, "context": 0.10}


async def score_symbol(symbol: str, timeframe: str, risk_profile: str = "balanced") -> Optional[dict]:
    """
    Run full analysis for one symbol on one timeframe.
    Returns a signal dict if confidence >= threshold, else None.
    """
    try:
        # Fetch data
        df = await binance.get_klines(symbol, timeframe, limit=500)
        if df is None or len(df) < 60:
            return None

        price = float(df["close"].iloc[-1])
        funding = await binance.get_funding_rate(symbol)
        fear_greed = await binance.get_fear_greed()
        news_score = await binance.get_news_sentiment(
            symbol, settings.cryptopanic_api_key
        )

        # Layer 1: Technical
        ta_res = ta_analyze(df)

        # Layer 2: Patterns
        pat_res = pattern_analyze(df)

        # Layer 3: ML
        if needs_training():
            logger.info(f"Training ML model on {symbol} {timeframe} data...")
            ml_train(df)
        ml_score = ml_predict(df)

        # Layer 4: Context
        context_score = _context_score(fear_greed["value"], funding, news_score)

        # Weighted combination
        combined = (
            ta_res["score"] * WEIGHTS["ta"]
            + pat_res["score"] * WEIGHTS["pattern"]
            + ml_score * WEIGHTS["ml"]
            + context_score * WEIGHTS["context"]
        )
        combined = max(-1.0, min(1.0, combined))
        confidence = abs(combined) * 100

        # Filter by threshold
        min_conf = settings.min_confidence(risk_profile)
        if confidence < min_conf:
            return None

        direction = "LONG" if combined > 0 else "SHORT"
        atr = float(pat_res["meta"].get("atr") or (df["high"] - df["low"]).rolling(14).mean().iloc[-1])

        # TP/SL using ATR multiples + nearest S/R
        if direction == "LONG":
            sl = price - atr * 1.5
            tp1 = price + atr * 2.0
            tp2 = price + atr * 3.5
            entry_low = price * 0.998
            entry_high = price * 1.002
        else:
            sl = price + atr * 1.5
            tp1 = price - atr * 2.0
            tp2 = price - atr * 3.5
            entry_low = price * 0.998
            entry_high = price * 1.002

        # Snap TP/SL to nearby S/R if close
        near_res = pat_res["meta"].get("nearest_resistance")
        near_sup = pat_res["meta"].get("nearest_support")
        if direction == "LONG" and near_res and abs(near_res - tp1) / tp1 < 0.01:
            tp1 = near_res * 0.998  # just below resistance
        if direction == "SHORT" and near_sup and abs(near_sup - tp1) / tp1 < 0.01:
            tp1 = near_sup * 1.002

        rr = abs(tp1 - price) / abs(sl - price) if abs(sl - price) > 0 else 0

        all_signals = ta_res["signals"] + pat_res["signals"]

        return {
            "symbol": symbol,
            "direction": direction,
            "timeframe": timeframe,
            "risk_profile": risk_profile,
            "created_at": datetime.utcnow().isoformat(),
            "entry_price": round(price, 4),
            "entry_low": round(entry_low, 4),
            "entry_high": round(entry_high, 4),
            "tp1": round(tp1, 4),
            "tp2": round(tp2, 4),
            "sl": round(sl, 4),
            "risk_reward": round(rr, 2),
            "confidence": round(confidence, 1),
            "ta_score": round(abs(ta_res["score"]) * 100, 1),
            "pattern_score": round(abs(pat_res["score"]) * 100, 1),
            "ml_score": round(abs(ml_score) * 100, 1),
            "context_score": round(abs(context_score) * 100, 1),
            "triggers": all_signals,
            "meta": {
                **ta_res["meta"],
                **pat_res["meta"],
                "funding_rate": round(funding * 100, 4),
                "fear_greed_value": fear_greed["value"],
                "fear_greed_label": fear_greed["label"],
                "news_sentiment": round(news_score, 3),
            },
        }

    except Exception as e:
        logger.error(f"score_symbol {symbol}/{timeframe} failed: {e}", exc_info=True)
        return None


def _context_score(fear_greed: int, funding_rate: float, news: float) -> float:
    """Combine market context indicators into [-1, 1]."""
    scores = []

    # Fear & Greed: extreme fear → buy signal, extreme greed → sell
    if fear_greed <= 20:
        scores.append(0.8)
    elif fear_greed <= 35:
        scores.append(0.4)
    elif fear_greed >= 80:
        scores.append(-0.8)
    elif fear_greed >= 65:
        scores.append(-0.4)
    else:
        scores.append(0.0)

    # Funding rate: very positive = longs pay heavily = bearish pressure
    if funding_rate > 0.001:
        scores.append(-0.6)
    elif funding_rate > 0.0005:
        scores.append(-0.3)
    elif funding_rate < -0.0005:
        scores.append(0.6)
    else:
        scores.append(0.0)

    # News sentiment
    scores.append(news * 0.5)

    return float(np.mean(scores)) if scores else 0.0
