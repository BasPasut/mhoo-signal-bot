"""
Smart Money Concepts (SMC) indicator layer.

Identifies WHERE institutions trade, not just when momentum is happening.

Concepts implemented:
  1. Order Blocks (OB)      — Last impulse candle before a strong move.
                               Price returning to OBs = high-probability reversal.
  2. Fair Value Gaps (FVG)  — Price imbalances (3-candle gaps).
                               Price tends to return and fill these gaps.
  3. Liquidity Sweeps       — Price briefly breaks below swing low / above swing
                               high then closes back — stop hunt before reversal.
                               Historically 60–70% WR on their own.

Score contributions (added on top of main TA score, capped at +0.30):
  OB hit in direction    +0.10–0.15
  FVG in direction       +0.08
  Liquidity sweep        +0.20  (strongest signal)
"""
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


# ── Order Blocks ─────────────────────────────────────────────────────────────

def find_order_blocks(df: pd.DataFrame, lookback: int = 60, impulse_pct: float = 0.008) -> dict:
    """
    Scan the last `lookback` bars for valid Order Blocks.
    Bull OB = last bearish candle before a strong up impulse.
    Bear OB = last bullish candle before a strong down impulse.
    Returns {"bull": [...], "bear": [...]} — most recent first, max 3 each.
    """
    obs: dict = {"bull": [], "bear": []}
    n = len(df)

    for i in range(max(0, n - lookback - 5), n - 5):
        candle = df.iloc[i]
        future = df.iloc[i + 1: min(i + 6, n)]
        if future.empty:
            continue

        o = float(candle["open"])
        c = float(candle["close"])
        h = float(candle["high"])
        lo = float(candle["low"])
        body = abs(c - o)
        candle_range = h - lo
        if candle_range < 1e-10 or body < candle_range * 0.25:
            continue  # ignore spinning tops / dojis

        # Bull OB: bearish candle before strong up move
        if c < o:
            up = (float(future["high"].max()) - h) / h
            if up > impulse_pct:
                obs["bull"].append({
                    "high": h, "low": lo,
                    "mid": (h + lo) / 2,
                    "bars_ago": n - 1 - i,
                })

        # Bear OB: bullish candle before strong down move
        elif c > o:
            dn = (lo - float(future["low"].min())) / lo
            if dn > impulse_pct:
                obs["bear"].append({
                    "high": h, "low": lo,
                    "mid": (h + lo) / 2,
                    "bars_ago": n - 1 - i,
                })

    obs["bull"] = sorted(obs["bull"], key=lambda x: x["bars_ago"])[:3]
    obs["bear"] = sorted(obs["bear"], key=lambda x: x["bars_ago"])[:3]
    return obs


# ── Fair Value Gaps ──────────────────────────────────────────────────────────

def find_fvg(df: pd.DataFrame, lookback: int = 30, min_gap_pct: float = 0.15) -> dict:
    """
    FVG exists when candle[i-2].high < candle[i].low (bull)
    or candle[i-2].low > candle[i].high (bear).
    Returns {"bull": [...], "bear": [...]} — most recent first, max 3 each.
    """
    fvgs: dict = {"bull": [], "bear": []}
    n = len(df)

    for i in range(max(2, n - lookback), n):
        c1h = float(df["high"].iloc[i - 2])
        c1l = float(df["low"].iloc[i - 2])
        c3h = float(df["high"].iloc[i])
        c3l = float(df["low"].iloc[i])

        if c1h < c3l:  # bullish FVG
            gap_pct = (c3l - c1h) / c1h * 100
            if gap_pct >= min_gap_pct:
                fvgs["bull"].append({
                    "top": c3l, "bottom": c1h,
                    "mid": (c3l + c1h) / 2,
                    "gap_pct": round(gap_pct, 2),
                    "bars_ago": n - 1 - i,
                })

        if c1l > c3h:  # bearish FVG
            gap_pct = (c1l - c3h) / c1l * 100
            if gap_pct >= min_gap_pct:
                fvgs["bear"].append({
                    "top": c1l, "bottom": c3h,
                    "mid": (c1l + c3h) / 2,
                    "gap_pct": round(gap_pct, 2),
                    "bars_ago": n - 1 - i,
                })

    fvgs["bull"] = sorted(fvgs["bull"], key=lambda x: x["bars_ago"])[:3]
    fvgs["bear"] = sorted(fvgs["bear"], key=lambda x: x["bars_ago"])[:3]
    return fvgs


# ── Liquidity Sweeps ─────────────────────────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 25) -> list:
    """
    Detect recent stop hunts (sweeps):
    Bull sweep: wick broke below a swing low, candle closed back above → reversal up.
    Bear sweep: wick broke above a swing high, candle closed back below → reversal down.
    Returns list of {"type": "bull"/"bear", "level": float, "fresh": bool}.
    """
    n = len(df)
    swing_lows, swing_highs = [], []

    # Find swing highs/lows from 3-5 bars ago to lookback bars ago
    for i in range(3, min(lookback + 3, n - 3)):
        idx = n - i - 1
        if idx < 2 or idx >= n - 2:
            continue
        local_lo = float(df["low"].iloc[max(0, idx - 2): idx + 3].min())
        local_hi = float(df["high"].iloc[max(0, idx - 2): idx + 3].max())
        if float(df["low"].iloc[idx]) == local_lo:
            swing_lows.append(float(df["low"].iloc[idx]))
        if float(df["high"].iloc[idx]) == local_hi:
            swing_highs.append(float(df["high"].iloc[idx]))

    sweeps = []
    if not swing_lows and not swing_highs:
        return sweeps

    for look_back in [1, 2]:  # check last 2 bars
        bar = df.iloc[-look_back]
        bar_lo = float(bar["low"])
        bar_hi = float(bar["high"])
        bar_close = float(bar["close"])
        fresh = look_back == 1

        if swing_lows:
            nearest_low = min(swing_lows, key=lambda x: abs(x - bar_close))
            if bar_lo < nearest_low and bar_close > nearest_low:
                sweeps.append({"type": "bull", "level": nearest_low, "fresh": fresh})

        if swing_highs:
            nearest_high = min(swing_highs, key=lambda x: abs(x - bar_close))
            if bar_hi > nearest_high and bar_close < nearest_high:
                sweeps.append({"type": "bear", "level": nearest_high, "fresh": fresh})

    return sweeps[:2]


# ── Main entry ───────────────────────────────────────────────────────────────

def analyze_smc(df: pd.DataFrame, direction: int, atr: float) -> dict:
    """
    direction: 1 = looking for LONG signals, -1 = looking for SHORT signals.
    Returns {"score": float, "signals": list, "meta": dict}.
    Score is a boost value [0, 0.30] to add to the main TA score.
    """
    price = float(df["close"].iloc[-1])
    smc_score = 0.0
    smc_signals = []
    tol = atr * 0.6

    try:
        obs = find_order_blocks(df)
        fvgs = find_fvg(df)
        sweeps = detect_liquidity_sweep(df)

        # ── Order Block scoring ───────────────────────────────────────────
        target_obs = obs["bull"] if direction == 1 else obs["bear"]
        for ob in target_obs:
            ob_lo = ob["low"] - tol
            ob_hi = ob["high"] + tol
            if ob_lo <= price <= ob_hi:
                # Closer to midpoint = stronger signal
                proximity = max(0.0, 1.0 - abs(price - ob["mid"]) / (atr * 2.0))
                boost = 0.12 * proximity
                smc_score += boost
                label = "Bullish" if direction == 1 else "Bearish"
                smc_signals.append({
                    "label": f"{label} Order Block @ ${ob['mid']:,.2f} (SMC)",
                    "dir": "long" if direction == 1 else "short",
                    "w": round(0.7 + 0.25 * proximity, 2),
                })
                break  # only count the nearest OB

        # ── Fair Value Gap scoring ────────────────────────────────────────
        target_fvgs = fvgs["bull"] if direction == 1 else fvgs["bear"]
        for fvg in target_fvgs:
            if fvg["bottom"] - tol * 0.5 <= price <= fvg["top"] + tol * 0.5:
                smc_score += 0.08
                label = "Bullish" if direction == 1 else "Bearish"
                smc_signals.append({
                    "label": f"{label} FVG ({fvg['gap_pct']:.1f}% gap) (SMC)",
                    "dir": "long" if direction == 1 else "short",
                    "w": 0.70,
                })
                break

        # ── Liquidity sweep scoring ───────────────────────────────────────
        target_type = "bull" if direction == 1 else "bear"
        for sweep in sweeps:
            if sweep["type"] == target_type:
                boost = 0.22 if sweep["fresh"] else 0.12
                smc_score += boost
                label = "Bullish" if direction == 1 else "Bearish"
                freshness = "FRESH " if sweep["fresh"] else ""
                smc_signals.append({
                    "label": f"{freshness}{label} Liquidity Sweep @ ${sweep['level']:,.2f} (SMC)",
                    "dir": "long" if direction == 1 else "short",
                    "w": 0.95 if sweep["fresh"] else 0.80,
                })

    except Exception as e:
        logger.debug(f"SMC analysis error: {e}")

    smc_score = min(0.30, smc_score)

    return {
        "score": smc_score,
        "signals": smc_signals,
        "meta": {
            "smc_bull_obs": len(obs.get("bull", [])) if "obs" in dir() else 0,
            "smc_bear_obs": len(obs.get("bear", [])) if "obs" in dir() else 0,
        },
    }
