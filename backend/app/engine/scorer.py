"""
Signal Scorer  v8-Ultimate
Combines the 4 analysis layers into a final confidence score and signal.

Weights:
  Technical indicators  75%  (3-layer MTF confluence)
  Chart patterns        15%
  ML model               5%
  Market context         5%

v8 Risk Management:
  Tier 1 (BTC/ETH)  : max 20x, 10% position budget
  Tier 2 (Top-20)   : max 10x, 10% position budget
  Tier 3 (Alts/Meme): max  5x,  7.5% position budget

  SL primary   = vol_tier × 1H ATR
  SL surgical  = nearest 15m fractal swing      (kicks in when ATR-SL > 2.5% of price)
  SL noise flo = max(structural, N×ATR_15m)     (Tier1: 1.5×; Tier2/3: 2.0× — anti stop-hunt)
  TP1          = max(1.0× ATR, 1.5× SL_dist)   (guaranteed R/R ≥ 1:1.5)
  TP2          = TP1 + 1.0× ATR                 (runner extension)
  On TP1 hit   → SL moves to entry + fees (breakeven)
  Leverage     = floor(Position_Budget / SL_Pct) (min 2x — skip signal if below)
"""
import asyncio
import numpy as np
import pandas as pd
import logging
from datetime import datetime
from typing import Optional

from app.engine.indicators.technical import analyze as ta_analyze
import ta as _ta
from app.engine.patterns.chart import analyze as pattern_analyze
from app.engine.ml.model import predict as ml_predict, train as ml_train, needs_training
from app.engine import binance
from app.engine.performance import confidence_adjustment
from app.core.settings import settings

logger = logging.getLogger(__name__)


# ── Task 2: Liquidity Tiering ─────────────────────────────────────────────────
# 3 tiers with hard max leverage caps and tier-specific equity-risk budgets.
# Leverage formula: floor(equity_risk / sl_pct) — see _calc_leverage_v7()

_TIER1 = frozenset({"BTC", "ETH"})
_TIER2 = frozenset({
    "BNB", "SOL", "XRP", "ADA", "AVAX", "DOT", "LINK",
    "MATIC", "POL", "TRX", "TON", "DOGE", "LTC", "UNI",
    "ATOM", "FIL", "NEAR", "FTM", "ARB", "OP", "APT",
    "SUI", "INJ", "WLD", "CFX",
})
# Everything else (meme coins, micro-caps) → Tier 3

_TIER_CONFIG: dict[int, dict] = {
    1: {"max_lev": 20, "equity_risk": 0.10},    # 10% position budget → ~6-20x typical
    2: {"max_lev": 10, "equity_risk": 0.10},    # 10% position budget → ~5-10x typical
    3: {"max_lev":  5, "equity_risk": 0.075},   #  7.5% position budget → ~2-5x typical
}


def _get_tier(symbol: str) -> int:
    """Map a trading symbol to its liquidity tier (1/2/3)."""
    sym = symbol.upper().replace("USDT", "").replace("BUSD", "").replace("1000", "")
    if sym in _TIER1:
        return 1
    if sym in _TIER2:
        return 2
    return 3


# ATR multiplier for initial SL sizing (1H ATR anchor).
# Wider multipliers = wider stops = lower leverage via the firewall.
# Surgical stop logic can override this when it yields < 3x.
_VOL_TIER: dict[str, float] = {
    "BTC": 1.5,  "ETH": 1.5,
    "BNB": 2.0,  "SOL": 2.0,  "XRP": 2.0,  "ADA": 2.0,
    "AVAX": 2.0, "DOT": 2.0,  "LINK": 2.0, "TRX": 2.0,
    "TON": 2.0,  "ATOM": 2.0, "NEAR": 2.0, "ARB": 2.0,
    "OP": 2.0,   "APT": 2.0,  "SUI": 2.0,  "INJ": 2.0,
    "WLD": 2.0,  "FIL": 2.0,  "FTM": 2.0,  "LTC": 2.0,
    "UNI": 2.0,  "MATIC": 2.0, "POL": 2.0,
    "DOGE": 2.5, "SHIB": 3.0, "PEPE": 3.0, "FLOKI": 3.0,
    "BONK": 3.0, "WIF": 2.5,  "BOME": 3.0,
    "CFX":  2.0,
}
_DEFAULT_VOL_TIER = 3.0


# ── Task 4: Leverage Firewall ─────────────────────────────────────────────────

def _calc_leverage_v7(sl_pct: float, equity_risk: float, tier_max_lev: int) -> int:
    """
    v7 Hard Firewall: Leverage = floor(Equity_Risk_Pct / SL_Pct)
    Hard cap at tier ceiling. Minimum 1x. Never suggests > 1x if math violates equity cap.
    """
    if sl_pct <= 0:
        return 1
    return max(1, min(tier_max_lev, int(equity_risk / sl_pct)))


# ── Task 3: Surgical Stop Loss ────────────────────────────────────────────────

def _surgical_swing_sl(df: pd.DataFrame, direction: str, lookback: int = 20) -> float:
    """
    Nearest confirmed fractal swing low (LONG) or high (SHORT) in the last `lookback` bars.
    Fractal: bar[i] low is lower than both neighbours (right-side confirmation required).
    Scans newest-first so the tightest (most recent) structural level is returned.
    Falls back to the lookback min/max if no clean fractal is found.
    """
    lows  = df["low"].values
    highs = df["high"].values
    n     = len(lows)
    if n < 5:
        return float(lows.min()) if direction == "LONG" else float(highs.max())

    start = max(2, n - lookback)
    if direction == "LONG":
        for i in range(n - 3, start - 1, -1):
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                return float(lows[i])
        return float(np.min(lows[start:]))
    else:
        for i in range(n - 3, start - 1, -1):
            if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                return float(highs[i])
        return float(np.max(highs[start:]))


# ── Position sizing ───────────────────────────────────────────────────────────

def _position_risk_pct(confidence: float, rr: float, risk_profile: str,
                       equity_risk_cap: float = 0.02) -> float:
    """
    Fixed risk % per signal tier (ALPHA/PRIME/SETUP), scaled by risk profile.

    Replaces Fractional Kelly which was unreliable without sufficient historical
    win-rate data and produced over-sized positions (e.g. 5.86% per trade).

    Tier thresholds: ALPHA ≥ 80%, PRIME ≥ 60%, SETUP < 60%
    Defaults: ALPHA=1.5%, PRIME=1.0%, SETUP=0.5%
    Profile scaling: conservative×0.67, balanced×1.0, aggressive×1.33 (hard cap 5%)
    """
    from app.core.config_store import get_risk_per_tier
    risk_map = get_risk_per_tier()

    grade = "ALPHA" if confidence >= 80 else "PRIME" if confidence >= 60 else "SETUP"
    base_risk = risk_map.get(grade, 1.0)

    profile_scale = {"conservative": 0.67, "balanced": 1.0, "aggressive": 1.33}
    scale = profile_scale.get(risk_profile, 1.0)

    return round(min(base_risk * scale, 5.0), 2)


# ── Bars-per-hour lookup (ML feature store) ───────────────────────────────────

_BARS_PER_HOUR: dict[str, float] = {
    "1m": 60, "3m": 20, "5m": 12, "15m": 4, "30m": 2,
    "1h": 1, "2h": 0.5, "4h": 0.25, "6h": 1/6, "12h": 1/12, "1d": 1/24,
}


def _snapshot_features(df: pd.DataFrame, meta: dict, regime: str, timeframe: str) -> dict:
    """
    Build the ML feature snapshot from already-loaded kline data.
    Called in score_symbol() before returning — zero extra API calls.
    All values are Python scalars (float/int/str/None).
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]
    now = datetime.utcnow()

    bph = _BARS_PER_HOUR.get(timeframe, 1)

    def _pct(hours: float) -> Optional[float]:
        bars = max(1, round(bph * hours))
        if len(c) > bars:
            return round(float((c.iloc[-1] - c.iloc[-1 - bars]) / c.iloc[-1 - bars] * 100), 3)
        return None

    def _ema_gap(window: int) -> Optional[float]:
        try:
            ema = _ta.trend.EMAIndicator(c, window).ema_indicator().iloc[-1]
            return round(float((c.iloc[-1] - ema) / (ema + 1e-10) * 100), 4)
        except Exception:
            return None

    try:
        rsi_s = _ta.momentum.RSIIndicator(c, 14).rsi()
        rsi_14 = round(float(rsi_s.iloc[-1]), 2)
        rsi_slope = round(float(rsi_s.diff(3).iloc[-1]), 2)
    except Exception:
        rsi_14 = rsi_slope = None

    try:
        macd = _ta.trend.MACD(c)
        macd_line = round(float(macd.macd().iloc[-1]), 6)
        macd_sig = round(float(macd.macd_signal().iloc[-1]), 6)
        macd_hist_v = round(float(macd.macd_diff().iloc[-1]), 6)
        macd_hist_slope = round(float(macd.macd_diff().diff(3).iloc[-1]), 6)
    except Exception:
        macd_line = macd_sig = macd_hist_v = macd_hist_slope = None

    try:
        bb = _ta.volatility.BollingerBands(c, 20, 2)
        bbu = float(bb.bollinger_hband().iloc[-1])
        bbl = float(bb.bollinger_lband().iloc[-1])
        bb_pct = round(float((c.iloc[-1] - bbl) / (bbu - bbl + 1e-10)), 4)
        bb_width = round(float(bb.bollinger_wband().iloc[-1]), 4)
    except Exception:
        bb_pct = bb_width = None

    try:
        atr_v = _ta.volatility.AverageTrueRange(h, l, c, 14).average_true_range().iloc[-1]
        atr_pct = round(float(atr_v / (c.iloc[-1] + 1e-10) * 100), 4)
    except Exception:
        atr_pct = None

    try:
        adx_ind = _ta.trend.ADXIndicator(h, l, c, 14)
        adx_v = round(float(adx_ind.adx().iloc[-1]), 2)
        adx_pos = round(float(adx_ind.adx_pos().iloc[-1]), 2)
        adx_neg = round(float(adx_ind.adx_neg().iloc[-1]), 2)
    except Exception:
        adx_v = adx_pos = adx_neg = None

    try:
        avg_v = v.rolling(20).mean().iloc[-1]
        vol_ratio = round(float(v.iloc[-1] / (avg_v + 1e-10)), 3)
        vol_trend = round(float((v.iloc[-1] - v.iloc[-4]) / (v.iloc[-4] + 1e-10) * 100), 2) if len(v) > 4 else None
    except Exception:
        vol_ratio = vol_trend = None

    try:
        o_val = float(df["open"].iloc[-1])
        c_val = float(c.iloc[-1])
        h_val = float(h.iloc[-1])
        l_val = float(l.iloc[-1])
        rng = h_val - l_val
        body_pct = round(abs(c_val - o_val) / (rng + 1e-10), 4) if rng > 0 else None
        upper_shd = round((h_val - max(o_val, c_val)) / (rng + 1e-10), 4) if rng > 0 else None
        lower_shd = round((min(o_val, c_val) - l_val) / (rng + 1e-10), 4) if rng > 0 else None
    except Exception:
        body_pct = upper_shd = lower_shd = None

    return {
        "price": round(float(c.iloc[-1]), 4),
        "price_change_1h": _pct(1),
        "price_change_4h": _pct(4),
        "price_change_24h": _pct(24),
        "regime": regime,
        "ema9_gap": _ema_gap(9),
        "ema21_gap": _ema_gap(21),
        "ema50_gap": _ema_gap(50),
        "ema200_gap": _ema_gap(200),
        "rsi_14": rsi_14,
        "rsi_slope_3": rsi_slope,
        "macd_line": macd_line,
        "macd_signal_line": macd_sig,
        "macd_hist": macd_hist_v,
        "macd_hist_slope": macd_hist_slope,
        "bb_pct": bb_pct,
        "bb_width": bb_width,
        "atr_pct": atr_pct,
        "adx": adx_v,
        "adx_pos": adx_pos,
        "adx_neg": adx_neg,
        "volume_ratio": vol_ratio,
        "volume_trend_3": vol_trend,
        "candle_body_pct": body_pct,
        "candle_upper_shadow": upper_shd,
        "candle_lower_shadow": lower_shd,
        "fear_greed": meta.get("fear_greed_value"),
        "funding_rate": meta.get("funding_rate"),
        "oi_change": meta.get("oi_change"),
        "hour_utc": now.hour,
        "day_of_week": now.weekday(),
    }


async def score_symbol(symbol: str, timeframe: str, risk_profile: str = "balanced") -> Optional[dict]:
    """
    Multi-Timeframe Confluence Analysis — v7-Ultimate.

    Entry path A (normal):    Layer 1 → Layer 2 → Layer 3 → Bonuses
    Entry path B (fast lane): Layer 1 → Fast Lane → Bonuses

    SL primary  : vol_tier × 1H ATR
    SL surgical : nearest 15m fractal (kicks in when ATR-SL > 2.5% of price)
    TP1         : max(1.0× ATR, 1.5× SL_dist) — R/R always ≥ 1:1.5
    TP2         : TP1 + 1.0× ATR
    Leverage    : floor(Equity_Risk_Pct / SL_Pct), min 2x; skip signal if below
    """
    try:
        df, df_1h, df_15m, df_4h = await asyncio.gather(
            binance.get_klines(symbol, timeframe,  limit=300),
            binance.get_klines(symbol, "1h",       limit=300),
            binance.get_klines(symbol, "15m",      limit=200),
            binance.get_klines(symbol, "4h",       limit=300),
        )

        if df is None or len(df) < 50:
            return None

        price = float(df["close"].iloc[-1])

        # Volume sanity guard — thin markets are stop-huntable and show false momentum
        _avg_vol = float(df["volume"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else float(df["volume"].iloc[-1])
        _vol_ratio = float(df["volume"].iloc[-1]) / (_avg_vol + 1e-10)
        if _vol_ratio < 0.05:
            logger.info(
                f"Skipping {symbol}/{timeframe}: low volume ({_vol_ratio:.2f}x avg) — "
                "thin market, entry unreliable"
            )
            return None

        funding, fear_greed, news_score, oi_change = await asyncio.gather(
            binance.get_funding_rate(symbol),
            binance.get_fear_greed(),
            binance.get_news_sentiment(symbol, settings.cryptopanic_api_key),
            binance.get_open_interest_change(symbol),
        )

        # Always use 15m as the entry TF for analysis — the 3-layer stack is
        # designed 4H→1H→15m regardless of how `score_symbol` was called.
        df_entry = df_15m if (df_15m is not None and len(df_15m) >= 50) else df
        ta_res      = ta_analyze(df_entry, df_1h=df_1h, df_4h=df_4h, oi_change=oi_change)
        pat_res     = pattern_analyze(df_entry)

        if needs_training(symbol, timeframe):
            logger.info(f"Training ML model [{symbol}/{timeframe}]...")
            ml_train(df, symbol, timeframe)
        ml_score = ml_predict(df, symbol, timeframe)

        context_score = _context_score(fear_greed["value"], funding, news_score, oi_change)

        if ta_res["score"] == 0.0:
            logger.info(
                f"Score {symbol}/{timeframe}: MTF filter blocked — "
                f"{ta_res['signals'][0]['label'] if ta_res['signals'] else 'unknown reason'}"
            )
            return None

        ta_direction  = 1 if ta_res["score"] > 0 else -1
        ml_effective  = ml_score if (ml_score * ta_direction > 0) else 0.0

        ta_conf       = abs(ta_res["score"]) * 100
        pattern_bonus = abs(pat_res["score"]) * 8 if pat_res["score"] * ta_direction > 0 else 0.0
        ml_bonus      = abs(ml_effective) * 3
        context_adj   = context_score * ta_direction * 3

        confidence = ta_conf + pattern_bonus + ml_bonus + context_adj
        confidence = max(0.0, min(100.0, confidence))

        perf_adj   = confidence_adjustment(symbol, timeframe)
        confidence = max(0.0, min(100.0, confidence + perf_adj))

        min_conf = settings.min_confidence(risk_profile)
        perf_str = f"{perf_adj:+.1f}pts" if perf_adj != 0.0 else "no data"
        logger.info(
            f"Score {symbol}/{timeframe}: confidence={confidence:.1f}% "
            f"(mtf={ta_conf:.1f}% +pat={pattern_bonus:.1f} +ml={ml_bonus:.1f} ctx={context_adj:+.1f} "
            f"perf={perf_str}) dir={'LONG' if ta_direction>0 else 'SHORT'} threshold={min_conf}%"
        )
        if confidence < min_conf:
            return None

        direction = "LONG" if ta_direction == 1 else "SHORT"

        # ── v7 Liquidity Tier ────────────────────────────────────────────────
        _FEE_RT      = 0.0008
        tier         = _get_tier(symbol)
        tier_cfg     = _TIER_CONFIG[tier]
        equity_risk  = tier_cfg["equity_risk"]
        tier_max_lev = tier_cfg["max_lev"]

        # 1H ATR is the primary volatility anchor
        if df_1h is not None and len(df_1h) >= 14:
            atr_1h = float((df_1h["high"] - df_1h["low"]).rolling(14).mean().iloc[-1])
        else:
            atr_1h = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])

        # ── v7 Task 3: Surgical Stop Loss ────────────────────────────────────
        # Direct price-level trigger: ATR-SL > 2.5% → switch to nearest 15m fractal swing.
        # Uses actual 15m kline data (fetched above) for precise structural levels.
        vol_mult      = _VOL_TIER.get(symbol.upper(), _DEFAULT_VOL_TIER)
        atr_sl_dist   = atr_1h * vol_mult
        atr_sl_pct    = atr_sl_dist / price if price > 0 else 0.025
        sl_method     = "atr_1h"
        sl_price_dist = atr_sl_dist          # final SL distance in price units

        if atr_sl_pct > 0.025:
            _df_sl = df_entry
            if _df_sl is not None and len(_df_sl) >= 10:
                swing_price = _surgical_swing_sl(_df_sl, direction)
                swing_dist  = abs(price - swing_price)
                swing_pct   = swing_dist / price
                if 0 < swing_pct < atr_sl_pct:
                    sl_price_dist = swing_dist
                    sl_method     = "structural_15m"
                    logger.info(
                        f"{symbol}: ATR-SL {atr_sl_pct*100:.2f}% > 2.5% → "
                        f"15m fractal SL {swing_pct*100:.2f}%"
                    )

        # ── v8 Volatility Noise Floor ─────────────────────────────────────────
        # Prevents stop-hunting: SL must sit outside the current 15m candle noise band.
        # Applied after fractal detection — noise floor is a minimum, not an override.
        # Tier 1: 1.5× ATR_15m (tighter — BTC/ETH have cleaner structure)
        # Tier 2/3: 2.0× ATR_15m (wider buffer for noisier mid/small-caps)
        if df_15m is not None and len(df_15m) >= 14:
            atr_15m = float((df_15m["high"] - df_15m["low"]).rolling(14).mean().iloc[-1])
        else:
            atr_15m = atr_1h / 4
        noise_mult  = 1.5 if tier == 1 else 2.0
        noise_floor = atr_15m * noise_mult
        if sl_price_dist < noise_floor:
            logger.info(
                f"{symbol}: SL noise-floored {sl_price_dist/price*100:.2f}% → "
                f"{noise_floor/price*100:.2f}% ({noise_mult:.1f}×ATR_15m)"
            )
            sl_price_dist = noise_floor
            sl_method     = "noise_floor"

        sl_pct = sl_price_dist / price

        # ── v8 Leverage Firewall ──────────────────────────────────────────────
        leverage = _calc_leverage_v7(sl_pct, equity_risk, tier_max_lev)

        # Minimum viable leverage gate: below 2x = unstable volatility, skip signal
        if leverage < 2:
            logger.info(
                f"Skipping {symbol}/{timeframe} — UNSTABLE VOLATILITY: "
                f"leverage {leverage}x unviable (sl={sl_pct*100:.2f}%)"
            )
            return None

        # ── v7 Task 5: ATR Scalp Targets ─────────────────────────────────────
        # TP1 guaranteed ≥ 1.5× SL distance so R/R ≥ 1:1.5 is structurally enforced.
        # TP2 extends exactly one ATR beyond TP1 (runner target).
        tp1_dist = max(atr_1h, sl_price_dist * 1.5)
        tp2_dist = tp1_dist + atr_1h

        tp1_pct  = tp1_dist / price
        net_gain = tp1_pct  - _FEE_RT
        net_loss = sl_pct   + _FEE_RT

        if net_gain <= 0:
            logger.info(f"Skipping {symbol}/{timeframe} — TP1 net of fees is zero")
            return None

        rr = round(net_gain / net_loss, 2) if net_loss > 0 else 0

        # Fetch live price to anchor all absolute levels at current market —
        # analysis ran on candle-close data which can be several minutes stale.
        try:
            live_price = await binance.get_current_price(symbol)
        except Exception:
            live_price = price

        if direction == "LONG":
            sl           = live_price - sl_price_dist
            tp1          = live_price + tp1_dist
            tp2          = live_price + tp2_dist
            breakeven_sl = round(live_price * (1 + _FEE_RT), 4)
            entry_low    = live_price * 0.997
            entry_high   = live_price * 1.001
        else:
            sl           = live_price + sl_price_dist
            tp1          = live_price - tp1_dist
            tp2          = live_price - tp2_dist
            breakeven_sl = round(live_price * (1 - _FEE_RT), 4)
            entry_low    = live_price * 0.999
            entry_high   = live_price * 1.003

        position_risk_pct = _position_risk_pct(confidence, rr, risk_profile, equity_risk)

        all_signals = ta_res["signals"] + pat_res["signals"]
        regime      = ta_res["meta"].get("htf_dir", 0)
        regime_str  = "bull" if regime == 1 else ("bear" if regime == -1 else "sideways")

        return {
            "symbol":        symbol,
            "direction":     direction,
            "timeframe":     "15m",
            "risk_profile":  risk_profile,
            "regime":        regime_str,
            "tier":          tier,
            "sl_method":     sl_method,
            "created_at":    datetime.utcnow().isoformat() + "Z",
            "entry_price":   round(live_price, 4),
            "entry_low":     round(entry_low, 4),
            "entry_high":    round(entry_high, 4),
            "tp1":           round(tp1, 4),
            "tp2":           round(tp2, 4),
            "sl":            round(sl, 4),
            "risk_reward":   round(rr, 2),
            "confidence":    round(confidence, 1),
            "leverage":      leverage,
            "ta_score":      round(ta_conf, 1),
            "pattern_score": round(abs(pat_res["score"]) * 100, 1),
            "ml_score":      round(abs(ml_score) * 100, 1),
            "context_score": round(abs(context_score) * 100, 1),
            "triggers":      all_signals,
            "position_risk_pct":  position_risk_pct,
            "breakeven_trigger":  breakeven_sl,   # entry+fees — SL moves here on TP1 hit
            "trailing_stop_atr":  1.0,
            "meta": {
                **ta_res["meta"],
                **pat_res["meta"],
                "funding_rate":     round(funding * 100, 4),
                "fear_greed_value": fear_greed["value"],
                "fear_greed_label": fear_greed["label"],
                "news_sentiment":   round(news_score, 3),
                "oi_change":        round(oi_change, 3),
                "tier":             tier,
                "sl_method":        sl_method,
                "atr_1h":           round(atr_1h, 6),
            },
            "_features": _snapshot_features(
                df_entry,
                {
                    "fear_greed_value": fear_greed["value"],
                    "funding_rate": round(funding * 100, 4),
                    "oi_change": round(oi_change, 3),
                },
                regime,
                "15m",
            ),
        }

    except Exception as e:
        logger.error(f"score_symbol {symbol}/{timeframe} failed: {e}", exc_info=True)
        return None


def _context_score(fear_greed: int, funding_rate: float, news: float, oi_change: float = 0.0) -> float:
    """Combine market context indicators into [-1, 1]."""
    scores = []
    weights = []

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
    weights.append(0.35)

    if funding_rate > 0.001:
        scores.append(-0.6)
    elif funding_rate > 0.0005:
        scores.append(-0.3)
    elif funding_rate < -0.0005:
        scores.append(0.6)
    elif funding_rate < -0.0001:
        scores.append(0.3)
    else:
        scores.append(0.0)
    weights.append(0.30)

    scores.append(oi_change * 0.8)
    weights.append(0.25)

    scores.append(news * 0.5)
    weights.append(0.10)

    return float(np.average(scores, weights=weights))
