import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


# ── Candlestick helpers ───────────────────────────────────────

def _body(df, i=-1): return abs(df["close"].iloc[i] - df["open"].iloc[i])
def _range(df, i=-1): return df["high"].iloc[i] - df["low"].iloc[i]
def _bull(df, i=-1): return df["close"].iloc[i] > df["open"].iloc[i]


def bullish_engulfing(df):
    return (not _bull(df, -2) and _bull(df, -1)
            and df["close"].iloc[-1] > df["open"].iloc[-2]
            and df["open"].iloc[-1] < df["close"].iloc[-2])


def bearish_engulfing(df):
    return (_bull(df, -2) and not _bull(df, -1)
            and df["close"].iloc[-1] < df["open"].iloc[-2]
            and df["open"].iloc[-1] > df["close"].iloc[-2])


def hammer(df):
    o, h, l, c = df["open"].iloc[-1], df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
    body = abs(c - o)
    if body == 0: return False
    lower = min(o, c) - l
    upper = h - max(o, c)
    return lower >= body * 2 and upper <= body * 0.5


def shooting_star(df):
    o, h, l, c = df["open"].iloc[-1], df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
    body = abs(c - o)
    if body == 0: return False
    upper = h - max(o, c)
    lower = min(o, c) - l
    return upper >= body * 2 and lower <= body * 0.5


def doji(df):
    r = _range(df)
    return r > 0 and _body(df) / r < 0.05


def morning_star(df):
    if len(df) < 3: return False
    c1 = not _bull(df, -3)
    c2 = _body(df, -2) < _range(df, -2) * 0.3
    c3 = _bull(df, -1)
    c4 = df["close"].iloc[-1] > (df["open"].iloc[-3] + df["close"].iloc[-3]) / 2
    return c1 and c2 and c3 and c4


def evening_star(df):
    if len(df) < 3: return False
    c1 = _bull(df, -3)
    c2 = _body(df, -2) < _range(df, -2) * 0.3
    c3 = not _bull(df, -1)
    c4 = df["close"].iloc[-1] < (df["open"].iloc[-3] + df["close"].iloc[-3]) / 2
    return c1 and c2 and c3 and c4


# ── Support / Resistance ──────────────────────────────────────

def find_sr_levels(df: pd.DataFrame, window=10):
    highs, lows = df["high"].values, df["low"].values
    res, sup = [], []
    for i in range(window, len(df) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            res.append(highs[i])
        if lows[i] == min(lows[i-window:i+window+1]):
            sup.append(lows[i])

    def cluster(lvls, tol=0.003):
        if not lvls: return []
        lvls = sorted(lvls)
        out, grp = [], [lvls[0]]
        for l in lvls[1:]:
            if (l - grp[-1]) / grp[-1] < tol:
                grp.append(l)
            else:
                out.append(float(np.mean(grp))); grp = [l]
        out.append(float(np.mean(grp)))
        return out

    return {"resistance": sorted(cluster(res), reverse=True)[:5],
            "support": sorted(cluster(sup), reverse=True)[:5]}


def check_breakout(df: pd.DataFrame, sr: dict, atr: float):
    price, prev = df["close"].iloc[-1], df["close"].iloc[-2]
    tol = atr * 0.5
    for r in sr["resistance"]:
        if prev < r and price > r + tol:
            return {"label": f"Breakout above resistance ${r:,.2f}", "dir": "long", "w": 0.9}
    for s in sr["support"]:
        if prev > s and price < s - tol:
            return {"label": f"Breakdown below support ${s:,.2f}", "dir": "short", "w": 0.9}
    return None


def get_trend(df: pd.DataFrame, lookback=20):
    r = df.tail(lookback)
    hh = sum(1 for i in range(1, len(r)) if r["high"].iloc[i] > r["high"].iloc[i-1])
    ll = sum(1 for i in range(1, len(r)) if r["low"].iloc[i] < r["low"].iloc[i-1])
    lh = sum(1 for i in range(1, len(r)) if r["high"].iloc[i] < r["high"].iloc[i-1])
    hl = sum(1 for i in range(1, len(r)) if r["low"].iloc[i] > r["low"].iloc[i-1])
    bull = hh + hl
    bear = lh + ll
    if bull > bear * 1.4: return "uptrend"
    if bear > bull * 1.4: return "downtrend"
    return "sideways"


# ── Main entry ────────────────────────────────────────────────

def analyze(df: pd.DataFrame) -> dict:
    if len(df) < 20:
        return {"score": 0.0, "signals": [], "confidence_pct": 0, "meta": {}}

    atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
    sr = find_sr_levels(df)
    trend = get_trend(df)
    signals, scores = [], []

    # Candlesticks
    checks = [
        (bullish_engulfing, "Bullish engulfing candle", "long", 0.8),
        (bearish_engulfing, "Bearish engulfing candle", "short", 0.8),
        (hammer, "Hammer reversal candle", "long", 0.7),
        (shooting_star, "Shooting star candle", "short", 0.7),
        (morning_star, "Morning star pattern", "long", 0.85),
        (evening_star, "Evening star pattern", "short", 0.85),
    ]
    for fn, label, direction, weight in checks:
        if fn(df):
            signals.append({"label": label, "dir": direction, "w": weight})
            scores.append(weight if direction == "long" else -weight)

    if doji(df) and trend != "sideways":
        signals.append({"label": "Doji — possible reversal", "dir": "neutral", "w": 0.4})

    # S/R breakout
    bo = check_breakout(df, sr, atr)
    if bo:
        signals.append(bo)
        scores.append(bo["w"] if bo["dir"] == "long" else -bo["w"])

    # Trend alignment bonus
    if trend == "uptrend" and scores and np.mean(scores) > 0:
        scores.append(0.3)
        signals.append({"label": "Uptrend structure confirmed", "dir": "long", "w": 0.3})
    elif trend == "downtrend" and scores and np.mean(scores) < 0:
        scores.append(-0.3)
        signals.append({"label": "Downtrend structure confirmed", "dir": "short", "w": 0.3})

    raw = float(np.mean(scores)) if scores else 0.0
    raw = max(-1.0, min(1.0, raw))

    price = df["close"].iloc[-1]
    above = [r for r in sr["resistance"] if r > price]
    below = [s for s in sr["support"] if s < price]

    return {
        "score": raw,
        "signals": signals,
        "confidence_pct": round(abs(raw) * 100, 1),
        "meta": {
            "trend": trend,
            "nearest_resistance": min(above) if above else None,
            "nearest_support": max(below) if below else None,
            "atr": round(float(atr), 4),
        },
    }
