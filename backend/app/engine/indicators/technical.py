"""
Multi-Timeframe Confluence Analysis  — v6

3-layer filter (or Fast Lane bypass):

  Layer 0 — Macro Gate    (Daily):  Resampled from 4H — no extra API call. Two sub-gates:
                                    EMA Gate : price vs EMA20, EMA20 slope ±0.5%/5d, price vs EMA50
                                    Structure: HH+HL in last 14 days (LONG) / LH+LL (SHORT)
  Layer 1 — HTF Filter    (4H)  :  EMA200 direction + ADX > 27 (raised from 25)
                                    ATR-expansion RSI guard (replaces fixed 82/18)
  Layer 2 — CTF Confirm   (1H)  :  MACD histogram positive AND rising (bull)
                                    or negative AND falling (bear) — strict, no recovery shortcuts
  Layer 3 — Entry Trigger (15m) :  BB Squeeze — volatility compresses then expands
                                    + volume spike > 1.5x last-10-bar avg
                                    (replaces RSI-SMA cross: fires at the elbow,
                                     not mid-move; self-filters choppy conditions)

  Fast Lane (bypass Layers 2+3)  :  4H impulse candle  >2×ATR
                                    + 15m retest of candle's 50% level
                                    + volume > 3x (raised from 1.5x — filters FOMO/bull-trap entries)

Confidence bonuses (on top of 33 pts per layer):
  SMC OB/FVG zone gate  :  +12 pts (OB) / +8 pts (FVG) / −10 pts (no zone)
  Liquidity sweep        :  +4–12 pts (amplified in ICT Kill Zones)
                            +15 pts if BB Squeeze entry fires after the sweep (Sweep Filter)
  Session scoring        :  +5 pts (London/NY Open), +3 pts (London Close)
                            −5 pts (Asian dead zone 05–12 UTC)
                            −3 pts (late US / pre-London 20–02 UTC)
  EMA stack 9/21/50      :  +2 pts
  4H ADX acceleration    :  +1 pt
  OI expansion           :  +1 pt
  VWAP side              :  +1 pt

SL  : 1H swing low (LONG) / 1H swing high (SHORT)
TP1 : 2.5× risk distance  → 29% WR breaks even
TP2 : 4.5× risk distance  → runner target
"""
import pandas as pd
import numpy as np
import ta
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_ADX_MIN          = 12   # HTF trend filter (relaxed for data collection)
_VOL_MULT         = 1.5  # entry trigger (BB Squeeze) volume multiplier
_FAST_LANE_VOL    = 3.0  # Fast Lane requires massive volume — filters FOMO/bull-trap impulses


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    return (typical * vol).rolling(window).sum() / vol.rolling(window).sum()


def _swing_low(df: pd.DataFrame, lookback: int = 20) -> float:
    """Lowest low in last `lookback` bars — structural SL for LONGs."""
    return float(df["low"].iloc[-lookback:].min())


def _swing_high(df: pd.DataFrame, lookback: int = 20) -> float:
    """Highest high in last `lookback` bars — structural SL for SHORTs."""
    return float(df["high"].iloc[-lookback:].max())


def _session_score() -> tuple[int, str]:
    """
    Time-of-day confidence modifier based on institutional session activity.

    Kill Zones (peak liquidity):
      London Open   02:00–05:00 UTC  →  +5 pts
      NY Open       12:00–16:00 UTC  →  +5 pts
      London Close  19:00–21:00 UTC  →  +3 pts

    v7 Task 7: ALL non-Kill-Zone windows → −15 pts (was −3/−5).
    Rationale: institutional liquidity is required for reliable signal execution;
    a −15 penalty effectively suppresses signals outside active hours.
    """
    h = datetime.now(timezone.utc).hour

    if 2 <= h < 5:
        return  5, "London Open Kill Zone (+5)"
    if 12 <= h < 16:
        return  5, "NY Open Kill Zone (+5)"
    if 19 <= h < 21:
        return  3, "London Close Kill Zone (+3)"
    # v7: unified hard penalty for all non-kill-zone windows
    return -15, "Outside Kill Zone — institutional liquidity absent (−15)"


def _is_kill_zone() -> bool:
    pts, _ = _session_score()
    return pts > 0


# ── Layer 1: HTF Filter (4H) — ATR-Expansion RSI Guard ───────────────────────

def _htf_filter(df_4h: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (direction, reason):
      +1 = HTF bullish  (price > EMA200 and ADX > 25 and DI+ dominant)
      -1 = HTF bearish  (price < EMA200 and ADX > 25 and DI- dominant)
       0 = no clear trend

    RSI extreme guard (replaces fixed 82/18 threshold):
      If RSI is at an extreme but ATR is expanding + volume surging
      → momentum regime → allow entry (strong-get-stronger)
      If RSI is at an extreme but ATR is flat/contracting
      → exhaustion regime → block entry
    """
    if df_4h is None or len(df_4h) < 200:
        return 0, "HTF data missing or insufficient"

    close = df_4h["close"]
    high  = df_4h["high"]
    low   = df_4h["low"]
    price = float(close.iloc[-1])

    ema200  = float(ta.trend.EMAIndicator(close, 200).ema_indicator().iloc[-1])
    adx_ind = ta.trend.ADXIndicator(high, low, close, 14)
    adx = float(adx_ind.adx().iloc[-1])
    dmp = float(adx_ind.adx_pos().iloc[-1])
    dmn = float(adx_ind.adx_neg().iloc[-1])

    # ATR ratio computed early — shared by dynamic ADX gate and RSI guard below
    _atr_s     = ta.volatility.AverageTrueRange(high, low, close, 14).average_true_range()
    _atr_now   = float(_atr_s.iloc[-1])
    _atr_norm  = float(_atr_s.rolling(20).mean().iloc[-1]) + 1e-9
    _atr_ratio = _atr_now / _atr_norm

    # Dynamic ADX minimum: only raise (stricter) when volatility is expanding — never lower below baseline
    # Rationale: in volatile moves the trend must be stronger; in quiet markets keep baseline 27
    _adx_min = 15 if _atr_ratio > 1.2 else _ADX_MIN  # 15 when ATR expanding, 12 otherwise

    if adx < _adx_min:
        return 0, f"4H ADX {adx:.1f} < {_adx_min} (ATR {_atr_ratio:.2f}x, dynamic) — no trend"

    # ── Daily EMA20+50 macro gate (resamples existing 4H data — no extra API call) ──
    # Three checks for LONGs: price > EMA20, EMA20 slope rising, price > EMA50
    # Three checks for SHORTs: price < EMA20, EMA20 slope falling, price < EMA50
    try:
        daily_close = df_4h["close"].resample("D").last().dropna()
        if len(daily_close) >= 20:
            ema20_s = ta.trend.EMAIndicator(daily_close, 20).ema_indicator().dropna()
            daily_ema20 = float(ema20_s.iloc[-1])

            # EMA20 slope: % change over last 5 daily bars
            ema20_5d_ago = float(ema20_s.iloc[-6]) if len(ema20_s) >= 6 else daily_ema20
            ema20_slope  = (daily_ema20 - ema20_5d_ago) / (ema20_5d_ago + 1e-9) * 100

            # Daily EMA50 structural check (use if enough data)
            daily_ema50 = None
            if len(daily_close) >= 50:
                ema50_s = ta.trend.EMAIndicator(daily_close, 50).ema_indicator().dropna()
                daily_ema50 = float(ema50_s.iloc[-1])

            if price > ema200 and dmp > dmn:
                # LONG macro checks
                if price < daily_ema20:
                    return 0, (
                        f"Macro gate: price {price:,.2f} < Daily EMA20 {daily_ema20:,.2f} — daily trend bearish"
                    )
                if ema20_slope < -0.5:
                    return 0, (
                        f"Macro gate: Daily EMA20 declining {ema20_slope:.1f}% / 5d — macro momentum bearish"
                    )
                if daily_ema50 is not None and price < daily_ema50:
                    return 0, (
                        f"Macro gate: price {price:,.2f} < Daily EMA50 {daily_ema50:,.2f} — macro structure bearish"
                    )

            if price < ema200 and dmn > dmp:
                # SHORT macro checks
                if price > daily_ema20:
                    return 0, (
                        f"Macro gate: price {price:,.2f} > Daily EMA20 {daily_ema20:,.2f} — daily trend bullish"
                    )
                if ema20_slope > 0.5:
                    return 0, (
                        f"Macro gate: Daily EMA20 rising {ema20_slope:.1f}% / 5d — macro momentum bullish"
                    )
                if daily_ema50 is not None and price > daily_ema50:
                    return 0, (
                        f"Macro gate: price {price:,.2f} > Daily EMA50 {daily_ema50:,.2f} — macro structure bullish"
                    )
    except Exception:
        pass

    # ── Daily Market Structure Gate (v6) ─────────────────────────────────────────
    # LONG only if daily chart shows HH + HL in last 14 days (no dead-cat bounces).
    # SHORT only if daily chart shows LH + LL in last 14 days (no relief rallies).
    try:
        daily_hl = df_4h.resample("D").agg({"high": "max", "low": "min"}).dropna()
        if len(daily_hl) >= 15:
            recent_hh = float(daily_hl["high"].iloc[-7:].max())
            prior_hh  = float(daily_hl["high"].iloc[-14:-7].max())
            recent_ll = float(daily_hl["low"].iloc[-7:].min())
            prior_ll  = float(daily_hl["low"].iloc[-14:-7].min())

            if price > ema200 and dmp > dmn:
                if not (recent_hh > prior_hh and recent_ll > prior_ll):
                    return 0, (
                        f"Daily structure gate: no HH+HL (HH {recent_hh:,.2f} vs {prior_hh:,.2f}, "
                        f"HL {recent_ll:,.2f} vs {prior_ll:,.2f}) — dead-cat bounce risk"
                    )
            if price < ema200 and dmn > dmp:
                if not (recent_hh < prior_hh and recent_ll < prior_ll):
                    return 0, (
                        f"Daily structure gate: no LH+LL (LH {recent_hh:,.2f} vs {prior_hh:,.2f}, "
                        f"LL {recent_ll:,.2f} vs {prior_ll:,.2f}) — relief rally risk"
                    )
    except Exception:
        pass

    rsi_series = ta.momentum.RSIIndicator(close, 14).rsi()
    rsi_4h     = float(rsi_series.iloc[-1])

    # ATR expansion: reuse already-computed series (no redundant computation)
    atr_ratio     = _atr_ratio
    atr_expanding = atr_ratio > 1.3   # ATR 30%+ above norm = expansion

    vol        = df_4h["volume"]
    vol_ratio  = float(vol.iloc[-1]) / (float(vol.rolling(20).mean().iloc[-1]) + 1e-9)
    vol_expanding = vol_ratio > 1.5

    # RSI Z-score: how extreme is the RSI relative to its own recent distribution?
    rsi_mean = float(rsi_series.rolling(50).mean().iloc[-1])
    rsi_std  = float(rsi_series.rolling(50).std().iloc[-1]) + 1e-9
    rsi_z    = (rsi_4h - rsi_mean) / rsi_std

    # Momentum regime = ATR expanding AND volume surging
    momentum_regime = atr_expanding and vol_expanding

    if price > ema200 and dmp > dmn:
        if rsi_4h > 82:
            if momentum_regime and rsi_z < 2.0:
                return 1, (
                    f"4H bull PARABOLIC: RSI {rsi_4h:.0f} (z={rsi_z:.1f}) "
                    f"ATR {atr_ratio:.1f}×norm vol {vol_ratio:.1f}x — momentum regime"
                )
            return 0, (
                f"4H bull exhaustion: RSI {rsi_4h:.0f} (z={rsi_z:.1f}) "
                f"ATR {atr_ratio:.2f}× — expansion not confirmed"
            )
        return 1, f"4H bull: price > EMA200 | ADX {adx:.1f} | DI+ {dmp:.1f} > DI- {dmn:.1f}"

    elif price < ema200 and dmn > dmp:
        if rsi_4h < 18:
            if momentum_regime and rsi_z > -2.0:
                return -1, (
                    f"4H bear PARABOLIC: RSI {rsi_4h:.0f} (z={rsi_z:.1f}) "
                    f"ATR {atr_ratio:.1f}×norm — momentum regime"
                )
            return 0, (
                f"4H bear exhaustion: RSI {rsi_4h:.0f} (z={rsi_z:.1f}) "
                f"ATR {atr_ratio:.2f}× — expansion not confirmed"
            )
        return -1, f"4H bear: price < EMA200 | ADX {adx:.1f} | DI- {dmn:.1f} > DI+ {dmp:.1f}"

    return 0, "4H EMA200/DI alignment unclear"


# ── Layer 2: CTF Confirmation (1H) ────────────────────────────────────────────

def _ctf_confirm(df_1h: pd.DataFrame, htf_direction: int) -> tuple[bool, str]:
    """
    Bullish confirmation: 1H MACD histogram is green AND rising for 2 consecutive bars.
    Bearish confirmation: 1H MACD histogram is red AND falling (single bar sufficient).

    LONG requires 2-bar consecutive rise to filter dead-cat-bounce MACD flickers.
    SHORT keeps single-bar check — SHORT WR is already strong.
    """
    if df_1h is None or len(df_1h) < 50:
        return False, "1H data missing or insufficient"

    close  = df_1h["close"]
    macd   = ta.trend.MACD(close)
    hist   = macd.macd_diff()
    h_now  = float(hist.iloc[-1])
    h_prev = float(hist.iloc[-2])
    h_prev2 = float(hist.iloc[-3])

    rsi = float(ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1])

    if htf_direction == 1:
        if h_now > 0 and h_now > h_prev and h_prev > h_prev2:
            if rsi > 65:
                return False, f"1H MACD bullish but RSI overbought ({rsi:.0f}) — skip"
            return True, f"1H MACD bullish 2-bar rising ({h_prev2:.4f}→{h_prev:.4f}→{h_now:.4f}) | RSI {rsi:.0f}"
        return False, f"1H MACD not bullish or single-bar flicker (hist={h_now:.4f}, prev={h_prev:.4f}, prev2={h_prev2:.4f})"
    else:
        if h_now < 0 and h_now < h_prev:
            if rsi < 35:
                return False, f"1H MACD bearish but RSI oversold ({rsi:.0f}) — skip"
            return True, f"1H MACD bearish ({h_now:.4f} falling) | RSI {rsi:.0f}"
        return False, f"1H MACD not bearish (hist={h_now:.4f}, prev={h_prev:.4f})"


# ── Layer 3: Entry Trigger (15m) — Bollinger Band Squeeze ────────────────────

def _entry_trigger(df_entry: pd.DataFrame, htf_direction: int) -> tuple[bool, float, str]:
    """
    BB Squeeze: fire when volatility contracts then expands in the trade direction.

    Squeeze condition  : BB width was in the bottom 20th percentile of the last
                         50 bars within the last 5 candles (coiled spring).
    Expansion condition: BB width is now increasing over the last 3 bars.
    Direction alignment: price is above (LONG) or below (SHORT) the BB midline.
    Volume check       : current bar volume > 1.5x last-10-bar average (kept).

    Fallback — BB band breakout: even without a prior squeeze, if price closes
    outside the band with volume > 2x average, it's an institutional breakout.
    """
    if df_entry is None or len(df_entry) < 50:
        return False, 0.0, "Entry TF data missing or insufficient"

    close  = df_entry["close"]
    volume = df_entry["volume"]

    # Entry-TF RSI guard — don't enter SHORT into oversold or LONG into overbought
    _entry_rsi = float(ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1])
    if htf_direction == -1 and _entry_rsi < 35:
        return False, 0.0, f"Entry RSI oversold ({_entry_rsi:.0f}) — bounce risk on SHORT"
    if htf_direction == 1 and _entry_rsi > 65:
        return False, 0.0, f"Entry RSI overbought ({_entry_rsi:.0f}) — pullback risk on LONG"

    # BB metrics
    bb       = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_width = bb.bollinger_wband()          # % bandwidth
    bb_mid   = bb.bollinger_mavg()
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()

    width_arr = bb_width.dropna().values
    if len(width_arr) < 10:
        return False, 0.0, "Insufficient BB history"

    width_now  = float(width_arr[-1])
    width_prev = float(width_arr[-3]) if len(width_arr) >= 3 else width_now
    is_expanding = width_now > width_prev

    # 20th-percentile threshold over last 50 bars (or however many exist)
    lookback   = min(50, len(width_arr))
    pct_20     = float(np.percentile(width_arr[-lookback:], 20))
    was_squeezed = any(w < pct_20 for w in width_arr[-6:-1])  # last 5 completed bars

    price_now  = float(close.iloc[-1])
    mid_now    = float(bb_mid.iloc[-1])
    upper_prev = float(bb_upper.iloc[-2])
    lower_prev = float(bb_lower.iloc[-2])

    directional_ok = (htf_direction == 1 and price_now > mid_now) or \
                     (htf_direction == -1 and price_now < mid_now)

    # Volume
    vol_avg   = float(volume.iloc[-11:-1].mean())
    vol_now   = float(volume.iloc[-1])
    vol_ratio = vol_now / (vol_avg + 1e-10)
    vol_ok    = vol_ratio >= _VOL_MULT

    side = "above" if htf_direction == 1 else "below"

    # Primary: squeeze → expansion with direction + volume
    if was_squeezed and is_expanding and directional_ok and vol_ok:
        return (
            True, vol_ratio,
            f"BB Squeeze breakout: width {width_now:.2f}% expanding "
            f"from {pct_20:.2f}% floor | price {side} midline | vol {vol_ratio:.1f}x"
        )

    # Secondary: strong breakout outside band — no prior squeeze required
    band_breakout = (htf_direction == 1 and price_now > upper_prev) or \
                    (htf_direction == -1 and price_now < lower_prev)
    if band_breakout and vol_ratio > 2.0:
        return (
            True, vol_ratio,
            f"BB band breakout + high vol {vol_ratio:.1f}x"
        )

    # Diagnostics for logging
    if not was_squeezed:
        return False, vol_ratio, f"No BB squeeze (min width {min(width_arr[-6:-1]):.2f}% > {pct_20:.2f}% floor)"
    if not is_expanding:
        return False, vol_ratio, f"BB not expanding (now {width_now:.2f}% ≤ prev {width_prev:.2f}%)"
    if not directional_ok:
        return False, vol_ratio, f"Price not {side} BB midline ({price_now:.4f} vs {mid_now:.4f})"
    return False, vol_ratio, f"BB squeeze setup but low vol ({vol_ratio:.1f}x < {_VOL_MULT}x)"


# ── SMC-Hybrid helpers ────────────────────────────────────────────────────────

def _get_prev_day_levels(df_1h: pd.DataFrame) -> tuple[float | None, float | None]:
    """
    Return (prev_day_high, prev_day_low) using 1H bars grouped by UTC date.
    Needs at least 2 distinct calendar days in the data.
    """
    if df_1h is None or len(df_1h) < 24:
        return None, None
    try:
        dates = sorted(set(df_1h.index.date))
        if len(dates) < 2:
            return None, None
        prev = dates[-2]
        day_data = df_1h[df_1h.index.date == prev]
        return float(day_data["high"].max()), float(day_data["low"].min())
    except Exception:
        return None, None


def _detect_pdh_pdl_sweep(
    df: pd.DataFrame, pdh: float, pdl: float, htf_dir: int
) -> tuple[bool, str]:
    """
    Bullish sweep (htf_dir=1)  : price wicked below PDL then closed back above.
    Bearish sweep (htf_dir=-1) : price wicked above PDH then closed back below.
    Checked over the last 5 bars — must be recent.
    """
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    for i in range(-5, 0):
        if htf_dir == 1:
            if lows[i] < pdl and closes[i] > pdl:
                return True, f"PDL sweep @ ${pdl:,.4f} (wick → ${lows[i]:,.4f})"
        else:
            if highs[i] > pdh and closes[i] < pdh:
                return True, f"PDH sweep @ ${pdh:,.4f} (wick → ${highs[i]:,.4f})"

    return False, f"No PDH/PDL sweep (PDH={pdh:,.4f} PDL={pdl:,.4f})"


def _detect_mss(df: pd.DataFrame, htf_dir: int) -> tuple[bool, float, str]:
    """
    15m Market Structure Shift (Change of Character / CHOCH).

    Bullish MSS: find the most recent pullback low (last 30 bars, exclude
                 final 3); find the highest high BEFORE that low; if any of
                 the last 3 bars CLOSED above that high → structure has shifted.

    Bearish MSS: mirror logic using swing high → structure low.

    The 3-bar freshness window ensures we catch the shift as it happens,
    not 10 bars later.
    """
    n = len(df)
    if n < 33:
        return False, 0.0, "Insufficient data for MSS"

    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    lookback = min(30, n - 3)

    if htf_dir == 1:  # Bullish MSS
        recent_low_rel = int(np.argmin(lows[-(lookback + 3):-3]))
        recent_low_idx = n - (lookback + 3) + recent_low_rel

        start = max(0, recent_low_idx - 20)
        structure_high = float(np.max(highs[start:recent_low_idx + 1]))

        for i in range(-3, 0):
            if closes[i] > structure_high:
                return True, structure_high, \
                    f"Bullish MSS: closed above structure high ${structure_high:,.4f}"
        return False, structure_high, \
            f"No MSS: close {closes[-1]:,.4f} < structure high {structure_high:,.4f}"

    else:  # Bearish MSS
        recent_high_rel = int(np.argmax(highs[-(lookback + 3):-3]))
        recent_high_idx = n - (lookback + 3) + recent_high_rel

        start = max(0, recent_high_idx - 20)
        structure_low = float(np.min(lows[start:recent_high_idx + 1]))

        for i in range(-3, 0):
            if closes[i] < structure_low:
                return True, structure_low, \
                    f"Bearish MSS: closed below structure low ${structure_low:,.4f}"
        return False, structure_low, \
            f"No MSS: close {closes[-1]:,.4f} > structure low {structure_low:,.4f}"


def _rsi_in_fvg(
    df: pd.DataFrame, target_fvgs: list, htf_dir: int
) -> tuple[bool, str]:
    """
    Hybrid Layer 3 confirmation: RSI-SMA cross fires WHILE price is inside a FVG.
    More precise than a blind RSI cross — structural context required.
    """
    if not target_fvgs:
        return False, "No FVG for RSI-in-FVG check"

    close = df["close"]
    price = float(close.iloc[-1])

    in_fvg = False
    fvg_label = ""
    for fvg in target_fvgs:
        if fvg["bottom"] <= price <= fvg["top"]:
            in_fvg = True
            fvg_label = f"FVG ${fvg['bottom']:,.4f}–${fvg['top']:,.4f}"
            break

    if not in_fvg:
        return False, f"Price ${price:,.4f} not inside any FVG"

    rsi_s   = ta.momentum.RSIIndicator(close, 14).rsi()
    rsi_sma = rsi_s.rolling(14).mean()
    rsi_now  = float(rsi_s.iloc[-1])
    rsi_prev = float(rsi_s.iloc[-2])
    sma_now  = float(rsi_sma.iloc[-1])

    if htf_dir == 1:
        confirmed = (rsi_now > sma_now and rsi_prev <= sma_now) or \
                    (rsi_now > sma_now and rsi_now > rsi_prev)
        if confirmed:
            return True, f"RSI ({rsi_now:.0f}>{sma_now:.0f}) cross inside {fvg_label}"
        return False, f"RSI not crossing up inside FVG (rsi={rsi_now:.0f} sma={sma_now:.0f})"
    else:
        confirmed = (rsi_now < sma_now and rsi_prev >= sma_now) or \
                    (rsi_now < sma_now and rsi_now < rsi_prev)
        if confirmed:
            return True, f"RSI ({rsi_now:.0f}<{sma_now:.0f}) cross inside {fvg_label}"
        return False, f"RSI not crossing down inside FVG (rsi={rsi_now:.0f} sma={sma_now:.0f})"


# ── Fast Lane: bypass CTF+Entry lag on 4H impulse retest ─────────────────────

def _fast_lane_trigger(
    df_4h: pd.DataFrame,
    df_entry: pd.DataFrame,
    htf_direction: int,
) -> tuple[bool, str]:
    """
    Bypasses the slow MACD/RSI confirmation layers when:
      1. Last closed 4H candle body > 2× ATR  (institutional impulse candle)
      2. Current 15m price is retesting the 50% level of that candle (±0.5%)
      3. 15m volume > 1.5× average  (speed check — still required)

    This fires 18–22h earlier than the normal 3-layer path on the same setup.
    Awards 33 pts (one layer) instead of 66 (CTF+Entry) to reflect lower certainty.
    Fast Lane trades need SMC zone bonuses to reach the confidence threshold.
    """
    if df_4h is None or len(df_4h) < 20 or df_entry is None or len(df_entry) < 20:
        return False, ""

    # 4H impulse check
    highs_4h  = df_4h["high"].values
    lows_4h   = df_4h["low"].values
    opens_4h  = df_4h["open"].values
    closes_4h = df_4h["close"].values

    atr_4h   = float(np.mean(highs_4h[-15:] - lows_4h[-15:]))
    last_body = abs(closes_4h[-1] - opens_4h[-1])
    is_impulse = last_body > atr_4h * 2.0

    if not is_impulse:
        return False, ""

    # Directional impulse alignment
    if htf_direction == 1 and closes_4h[-1] < opens_4h[-1]:
        return False, ""   # bearish candle in a bull setup — wait
    if htf_direction == -1 and closes_4h[-1] > opens_4h[-1]:
        return False, ""   # bullish candle in a bear setup — wait

    # 50% retracement level of the impulse candle
    candle_mid = (closes_4h[-1] + opens_4h[-1]) / 2
    price_15m  = float(df_entry["close"].iloc[-1])
    within_retest = abs(price_15m - candle_mid) / (candle_mid + 1e-9) < 0.005

    if not within_retest:
        return False, ""

    # Volume speed check on 15m — must be 3x to confirm institutional flow (not FOMO)
    vol = df_entry["volume"]
    vol_avg   = float(vol.iloc[-11:-1].mean())
    vol_ratio = float(vol.iloc[-1]) / (vol_avg + 1e-10)
    if vol_ratio < _FAST_LANE_VOL:
        return False, ""

    return True, (
        f"Fast Lane: 4H impulse {last_body/atr_4h:.1f}×ATR "
        f"+ 50% retest @ {candle_mid:.4f} "
        f"+ vol {vol_ratio:.1f}x"
    )


# ── Main entry ────────────────────────────────────────────────────────────────

def analyze(
    df: pd.DataFrame,
    df_1h: pd.DataFrame | None = None,
    df_4h: pd.DataFrame | None = None,
    oi_change: float = 0.0,
) -> dict:
    """
    Multi-Timeframe Confluence Analysis v6.

    Entry path A (normal):    Layer 1 → Layer 2 → Layer 3 → Bonuses
    Entry path B (fast lane): Layer 1 → Fast Lane → Bonuses
                               (earns 33 pts instead of 66 for Layers 2+3;
                                needs SMC zone to reach threshold)
    """
    if len(df) < 50:
        return _zero(0, 0, 0, "bearish", 0, False, "Insufficient entry TF data")

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]
    price  = float(close.iloc[-1])

    vol_ma    = volume.rolling(20).mean()
    vol_ratio = float(volume.iloc[-1] / (float(vol_ma.iloc[-1]) + 1e-10))

    rsi_s = ta.momentum.RSIIndicator(close, 14).rsi()
    rsi   = float(rsi_s.iloc[-1])

    macd_ind = ta.trend.MACD(close)
    h_now = float(macd_ind.macd_diff().iloc[-1])

    ema9  = float(ta.trend.EMAIndicator(close, 9).ema_indicator().iloc[-1])
    ema21 = float(ta.trend.EMAIndicator(close, 21).ema_indicator().iloc[-1])
    ema_bias = "bullish" if ema9 > ema21 else "bearish"

    df_for_adx = df_4h if (df_4h is not None and len(df_4h) >= 50) else df
    try:
        adx_ind = ta.trend.ADXIndicator(df_for_adx["high"], df_for_adx["low"], df_for_adx["close"], 14)
        adx_val = float(adx_ind.adx().iloc[-1])
    except Exception:
        adx_val = 0.0

    above_ema200 = False
    if df_4h is not None and len(df_4h) >= 200:
        ema200_4h = float(ta.trend.EMAIndicator(df_4h["close"], 200).ema_indicator().iloc[-1])
        above_ema200 = float(df_4h["close"].iloc[-1]) > ema200_4h
    elif len(df) >= 200:
        ema200_e = float(ta.trend.EMAIndicator(close, 200).ema_indicator().iloc[-1])
        above_ema200 = price > ema200_e

    signals: list[dict] = []

    # ════════════════════════════════════════════════════════════════════════
    # LAYER 1: HTF Filter (4H) — ATR-expansion RSI guard
    # ════════════════════════════════════════════════════════════════════════
    htf_dir, htf_reason = _htf_filter(df_4h)

    if htf_dir == 0:
        return _zero(rsi, h_now, vol_ratio, ema_bias, adx_val, above_ema200,
                     f"HTF: {htf_reason}")

    dir_s = "long" if htf_dir == 1 else "short"
    signals.append({"label": htf_reason, "dir": dir_s, "w": 1.0})
    points = 33.0

    # ════════════════════════════════════════════════════════════════════════
    # LAYERS 2 + 3  — or Fast Lane bypass
    # ════════════════════════════════════════════════════════════════════════
    fast_ok, fast_reason = _fast_lane_trigger(df_4h, df, htf_dir)

    ctf_ok        = False   # initialised here so it's always defined for dedup logic below
    hybrid_l2     = False
    hybrid_l3     = False
    bb_squeeze_fired = False   # tracks if Layer 3 fired via pure BB Squeeze (for Sweep Filter)

    if fast_ok:
        # Fast Lane: bypass slow MACD/RSI confirmation, award one combined layer
        signals.append({"label": fast_reason, "dir": dir_s, "w": 0.85})
        points += 33.0
        entry_vol_ratio = vol_ratio
    else:
        # ── Layer 2: 1H MACD  OR  SMC-Hybrid (PDH/PDL sweep + MSS) ──────────
        ctf_ok, ctf_reason = _ctf_confirm(df_1h, htf_dir)

        if not ctf_ok:
            # MACD failed — try Hybrid Layer 2
            pdh, pdl = _get_prev_day_levels(df_1h)
            if pdh is not None:
                sweep_ok, sweep_reason = _detect_pdh_pdl_sweep(df, pdh, pdl, htf_dir)
                mss_ok, _mss_lvl, mss_reason = _detect_mss(df, htf_dir)
                if sweep_ok and mss_ok:
                    hybrid_l2 = True
                    ctf_reason = f"Hybrid L2 — {sweep_reason} | {mss_reason}"

        if not ctf_ok and not hybrid_l2:
            return _zero(rsi, h_now, vol_ratio, ema_bias, adx_val, above_ema200,
                         f"CTF: {ctf_reason}")

        w_l2 = 1.05 if hybrid_l2 else 0.9   # SMC structure = slightly higher weight
        signals.append({"label": ctf_reason, "dir": dir_s, "w": w_l2})
        points += 33.0

        # ── Layer 3: BB Squeeze  OR  RSI cross inside FVG (Hybrid) ──────────
        triggered, entry_vol_ratio, trigger_reason = _entry_trigger(df, htf_dir)

        if not triggered:
            # BB Squeeze failed — try RSI-in-FVG
            try:
                from app.engine.indicators.smc import find_fvg as _find_fvg
                _fvgs     = _find_fvg(df)
                _tgt_fvgs = _fvgs["bull"] if htf_dir == 1 else _fvgs["bear"]
                rfi_ok, rfi_reason = _rsi_in_fvg(df, _tgt_fvgs, htf_dir)
                if rfi_ok:
                    hybrid_l3 = True
                    trigger_reason = f"Hybrid L3 — {rfi_reason}"
                    entry_vol_ratio = vol_ratio
            except Exception:
                pass

        if not triggered and not hybrid_l3:
            return _zero(rsi, h_now, vol_ratio, ema_bias, adx_val, above_ema200,
                         f"Trigger: {trigger_reason}")

        w_l3 = 1.10 if hybrid_l3 else 1.0   # RSI-in-FVG = highest precision entry
        signals.append({"label": trigger_reason, "dir": dir_s, "w": w_l3})
        points += 33.0
        bb_squeeze_fired = triggered and not hybrid_l3

    # Full SMC-Hybrid bonus: BOTH L2 and L3 came from the Hybrid path
    if hybrid_l2 and hybrid_l3:
        points = min(100, points + 10)
        signals.append({
            "label": "Full SMC-Hybrid: PDH/PDL→MSS→FVG→RSI complete sequence",
            "dir": dir_s, "w": 1.5,
        })

    # ════════════════════════════════════════════════════════════════════════
    # BONUSES
    # ════════════════════════════════════════════════════════════════════════

    # ── OI expansion ─────────────────────────────────────────────────────
    if oi_change > 0.01:
        points = min(100, points + 1)

    # ── VWAP alignment ───────────────────────────────────────────────────
    try:
        vwap = float(_rolling_vwap(df).iloc[-1])
        if (htf_dir == 1 and price > vwap) or (htf_dir == -1 and price < vwap):
            points = min(100, points + 1)
            signals.append({"label": "Price on correct side of VWAP", "dir": dir_s, "w": 0.6})
    except Exception:
        pass

    # ── SMC Gate + Liquidity Sweep ────────────────────────────────────────
    # OB/FVG: +12 / +8 pts when price is inside a structural zone.
    # No zone found: −10 pts penalty (entry lacks structural support).
    # Sweep: +4–12 pts, amplified in ICT Kill Zones.
    smc_anchor_found = False   # v7 Task 7: tracked for regime cap below
    try:
        from app.engine.indicators.smc import find_order_blocks, find_fvg, detect_liquidity_sweep

        atr_e = float((high - low).rolling(14).mean().iloc[-1])
        tol   = atr_e * 0.6

        obs    = find_order_blocks(df)
        fvgs   = find_fvg(df)
        sweeps = detect_liquidity_sweep(df)

        target_obs    = obs["bull"]   if htf_dir == 1 else obs["bear"]
        target_fvgs   = fvgs["bull"]  if htf_dir == 1 else fvgs["bear"]
        target_sweeps = [s for s in sweeps if s["type"] == ("bull" if htf_dir == 1 else "bear")]

        in_ob  = any(ob["low"]  - tol       <= price <= ob["high"]  + tol       for ob  in target_obs)
        in_fvg = any(fvg["bottom"] - tol * 0.5 <= price <= fvg["top"] + tol * 0.5 for fvg in target_fvgs)

        lbl = "Bull" if htf_dir == 1 else "Bear"

        if in_ob:
            smc_anchor_found = True
            ob = target_obs[0]
            points = min(100, points + 12)
            signals.append({
                "label": f"{lbl} Order Block @ ${ob['mid']:,.4f} — SMC gate",
                "dir": dir_s, "w": 1.2,
            })
        elif in_fvg:
            smc_anchor_found = True
            fvg = target_fvgs[0]
            points = min(100, points + 8)
            signals.append({
                "label": f"{lbl} FVG ({fvg['gap_pct']:.1f}% gap) — SMC gate",
                "dir": dir_s, "w": 1.0,
            })
        else:
            points = max(0, points - 10)
            signals.append({
                "label": "No OB/FVG — structure-less entry (−10 pts)",
                "dir": "neutral", "w": -0.3,
            })

        # Liquidity sweep bonus — Sweep Filter: BB Squeeze after sweep = +15 (highest precision)
        in_kill_zone = _is_kill_zone()
        for sweep in target_sweeps[:1]:   # only strongest sweep
            if sweep["fresh"] and bb_squeeze_fired:
                sweep_pts = 15
                kz_tag = " [Sweep Filter]"
                w = 1.6
            elif sweep["fresh"] and in_kill_zone:
                sweep_pts = 12
                kz_tag = " [Kill Zone]"
                w = 1.4
            elif sweep["fresh"]:
                sweep_pts = 8
                kz_tag = ""
                w = 0.95
            else:
                sweep_pts = 4
                kz_tag = ""
                w = 0.70
            points = min(100, points + sweep_pts)
            signals.append({
                "label": f"{lbl} Liquidity Sweep @ ${sweep['level']:,.4f}{kz_tag}",
                "dir": dir_s, "w": w,
            })

    except Exception:
        pass

    # ── v7 Task 6: Confidence Decoupling ─────────────────────────────────
    # When MACD (L2 normal path) AND RSI-in-FVG (L3 hybrid) both fire,
    # they measure the same underlying momentum — award L3 only 20 pts
    # instead of 33 (−13 deduction) to remove the correlated double-count.
    macd_normal_fired = not fast_ok and ctf_ok and not hybrid_l2
    if macd_normal_fired and hybrid_l3:
        points = max(0, points - 13)
        signals.append({
            "label": "Momentum dedup: MACD+RSI-in-FVG correlated (−13 pts)",
            "dir": "neutral", "w": -0.2,
        })

    # ── Session time scoring (v7 Task 7) ─────────────────────────────────
    # Kill Zones: +5 pts.  Non-kill-zone: −15 pts (hard penalty).
    sess_pts, sess_label = _session_score()
    points = max(0, min(100, points + sess_pts))
    sess_dir = dir_s if sess_pts > 0 else "neutral"
    signals.append({"label": sess_label, "dir": sess_dir, "w": 0.6 if sess_pts > 0 else -0.4})

    # ── EMA stack alignment (9/21/50) ────────────────────────────────────
    # v7 Task 6: skip EMA bonus when MACD normal path already fired —
    # MACD direction and EMA stack alignment are collinear signals.
    try:
        ema50_v = float(ta.trend.EMAIndicator(close, 50).ema_indicator().iloc[-1])
        stack = (htf_dir == 1 and ema9 > ema21 > ema50_v) or \
                (htf_dir == -1 and ema9 < ema21 < ema50_v)
        if stack and not macd_normal_fired:
            points = min(100, points + 2)
            signals.append({"label": "EMA stack aligned (9/21/50)", "dir": dir_s, "w": 0.8})
        elif stack and macd_normal_fired:
            points = min(100, points + 1)   # half credit — MACD already captured directionality
            signals.append({"label": "EMA stack aligned (9/21/50) — partial (+1, MACD correlated)", "dir": dir_s, "w": 0.4})
    except Exception:
        pass

    # ── 4H ADX acceleration ───────────────────────────────────────────────
    try:
        if df_4h is not None and len(df_4h) >= 20:
            adx_s   = ta.trend.ADXIndicator(df_4h["high"], df_4h["low"], df_4h["close"], 14).adx()
            adx_now = float(adx_s.iloc[-1])
            adx_ago = float(adx_s.iloc[-4])
            if adx_now > adx_ago + 2.0:
                points = min(100, points + 1)
                signals.append({
                    "label": f"4H ADX accelerating ({adx_ago:.0f}→{adx_now:.0f})",
                    "dir": dir_s, "w": 0.5,
                })
    except Exception:
        pass

    # ── v7 Task 7: Regime Caps ────────────────────────────────────────────
    # Cap confidence at 65 if ADX < 25 (weak trend, high whipsaw risk)
    # Cap confidence at 65 if no SMC anchor (OB or FVG) was found
    if adx_val < 25:
        points = min(65.0, points)
        signals.append({
            "label": f"Regime cap: ADX {adx_val:.1f} < 25 — confidence ≤ 65",
            "dir": "neutral", "w": -0.3,
        })
    if not smc_anchor_found:
        points = min(65.0, points)
        signals.append({
            "label": "Regime cap: no SMC anchor (OB/FVG) — confidence ≤ 65",
            "dir": "neutral", "w": -0.3,
        })

    # ── Score conversion ──────────────────────────────────────────────────
    score = htf_dir * min(1.0, points / 100.0)

    return {
        "score": score,
        "signals": signals,
        "confidence_pct": round(abs(score) * 100, 1),
        "meta": {
            "rsi":              round(rsi, 2),
            "macd_hist":        round(h_now, 6),
            "volume_ratio":     round(vol_ratio, 2),
            "ema_bias":         ema_bias,
            "adx":              round(adx_val, 1),
            "above_ema200":     above_ema200,
            "htf_dir":          htf_dir,
            "fast_lane":        fast_ok,
            "kill_zone":        sess_pts > 0,
            "session_pts":      sess_pts,
            "smc_anchor":       smc_anchor_found,
            "macd_normal_fired": macd_normal_fired,
        },
    }


def _zero(rsi, macd_hist, vol_ratio, ema_bias, adx, above_ema200, reason: str) -> dict:
    return {
        "score": 0.0,
        "signals": [{"label": reason, "dir": "neutral", "w": 0}],
        "confidence_pct": 0,
        "meta": {
            "rsi":          round(float(rsi), 2),
            "macd_hist":    round(float(macd_hist), 6),
            "volume_ratio": round(float(vol_ratio), 2),
            "ema_bias":     ema_bias,
            "adx":          round(float(adx), 1),
            "above_ema200": above_ema200,
        },
    }
