import pandas as pd
import numpy as np
import ta
import logging

logger = logging.getLogger(__name__)


def analyze(df: pd.DataFrame) -> dict:
    """
    Run all technical indicators. Returns score [-1,1], signals list,
    and confidence_pct 0-100 for this layer.
    """
    if len(df) < 60:
        return {"score": 0.0, "signals": [], "confidence_pct": 0, "meta": {}}

    scores, signals = [], []
    close = df["close"]

    # ── RSI ──────────────────────────────────────────────────
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
    rv, rp = rsi.iloc[-1], rsi.iloc[-2]
    if rv < 30 and rv > rp:
        signals.append({"label": "RSI oversold bounce", "dir": "long", "w": 0.85})
        scores.append(0.85)
    elif rv > 70 and rv < rp:
        signals.append({"label": "RSI overbought reversal", "dir": "short", "w": 0.85})
        scores.append(-0.85)
    elif rv > 55:
        scores.append(0.3)
    elif rv < 45:
        scores.append(-0.3)

    # ── MACD ─────────────────────────────────────────────────
    macd = ta.trend.MACD(close)
    hist_now = macd.macd_diff().iloc[-1]
    hist_prev = macd.macd_diff().iloc[-2]
    if hist_prev < 0 and hist_now > 0:
        signals.append({"label": "MACD bullish crossover", "dir": "long", "w": 0.9})
        scores.append(0.9)
    elif hist_prev > 0 and hist_now < 0:
        signals.append({"label": "MACD bearish crossover", "dir": "short", "w": 0.9})
        scores.append(-0.9)
    elif hist_now > 0:
        scores.append(0.35)
    else:
        scores.append(-0.35)

    # ── Bollinger Bands ───────────────────────────────────────
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    price = close.iloc[-1]
    bbu, bbl = bb.bollinger_hband().iloc[-1], bb.bollinger_lband().iloc[-1]
    bbw = bb.bollinger_wband().iloc[-1]
    bbw_avg = bb.bollinger_wband().rolling(20).mean().iloc[-1]
    if price <= bbl:
        signals.append({"label": "Price at lower Bollinger Band", "dir": "long", "w": 0.75})
        scores.append(0.75)
    elif price >= bbu:
        signals.append({"label": "Price at upper Bollinger Band", "dir": "short", "w": 0.75})
        scores.append(-0.75)
    if bbw_avg and bbw < bbw_avg * 0.7:
        signals.append({"label": "Bollinger squeeze — breakout pending", "dir": "neutral", "w": 0.4})

    # ── EMA alignment ────────────────────────────────────────
    ema = {p: ta.trend.EMAIndicator(close, window=p).ema_indicator() for p in [9, 21, 50, 200]}
    e9, e21, e50, e200 = (ema[p].iloc[-1] for p in [9, 21, 50, 200])
    if len(df) >= 200:
        if e9 > e21 > e50 > e200:
            signals.append({"label": "Full EMA bullish stack (9>21>50>200)", "dir": "long", "w": 0.95})
            scores.append(0.95)
        elif e9 < e21 < e50 < e200:
            signals.append({"label": "Full EMA bearish stack (9<21<50<200)", "dir": "short", "w": 0.95})
            scores.append(-0.95)
        elif e9 > e21 and price > e50:
            scores.append(0.45)
        elif e9 < e21 and price < e50:
            scores.append(-0.45)
    # EMA 21 cross
    e21_prev = ema[21].iloc[-2]
    c_prev = close.iloc[-2]
    if c_prev < e21_prev and price > e21:
        signals.append({"label": "Price crossed above EMA 21", "dir": "long", "w": 0.65})
        scores.append(0.65)
    elif c_prev > e21_prev and price < e21:
        signals.append({"label": "Price crossed below EMA 21", "dir": "short", "w": 0.65})
        scores.append(-0.65)

    # ── Volume spike ─────────────────────────────────────────
    vol_ratio = df["volume"].iloc[-1] / df["volume"].rolling(20).mean().iloc[-1]
    if vol_ratio > 2.5:
        dominant_dir = "long" if (scores and np.mean(scores) > 0) else "short"
        signals.append({"label": f"Volume spike +{int((vol_ratio-1)*100)}% vs avg", "dir": dominant_dir, "w": 0.6})
        scores.append(0.5 if dominant_dir == "long" else -0.5)

    # ── ADX trend strength ───────────────────────────────────
    if len(df) >= 30:
        adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], close, window=14)
        adx_val = adx_ind.adx().iloc[-1]
        if adx_val > 25 and scores:
            bonus = 0.2 * np.sign(np.mean(scores))
            scores.append(bonus)
            signals.append({"label": f"Strong trend (ADX {adx_val:.0f})", "dir": "long" if bonus > 0 else "short", "w": 0.5})

    # ── Stochastic ───────────────────────────────────────────
    stoch = ta.momentum.StochasticOscillator(df["high"], df["low"], close)
    sk = stoch.stoch().iloc[-1]
    sd = stoch.stoch_signal().iloc[-1]
    sk_p = stoch.stoch().iloc[-2]
    if sk < 20 and sk > sk_p and sk > sd:
        signals.append({"label": "Stochastic bullish crossover (oversold)", "dir": "long", "w": 0.65})
        scores.append(0.65)
    elif sk > 80 and sk < sk_p and sk < sd:
        signals.append({"label": "Stochastic bearish crossover (overbought)", "dir": "short", "w": 0.65})
        scores.append(-0.65)

    # ── Final ─────────────────────────────────────────────────
    raw = float(np.mean(scores)) if scores else 0.0
    raw = max(-1.0, min(1.0, raw))

    return {
        "score": raw,
        "signals": signals,
        "confidence_pct": round(abs(raw) * 100, 1),
        "meta": {
            "rsi": round(float(rv), 2),
            "macd_hist": round(float(hist_now), 6),
            "bb_pct": round((price - bbl) / (bbu - bbl + 1e-10), 3) if bbu != bbl else 0.5,
            "volume_ratio": round(float(vol_ratio), 2),
            "ema_bias": "bullish" if e9 > e21 else "bearish",
        },
    }
