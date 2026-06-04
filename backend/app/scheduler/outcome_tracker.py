"""
Outcome tracker — periodically checks open signals against klines data
and labels them WIN / LOSS / EXPIRED so the ML model can learn from results.

v2 improvements over the original:
  - Checks every 60 s (was 300 s) for faster resolution
  - Uses per-candle OHLC scan instead of spot price — catches intrabar TP/SL touches
  - SL-first check within each candle (conservative / realistic)
  - Uses exact TP1 or SL price as the exit price (not a delayed spot reading)

On resolution:
  - Stores result_at, result_price
  - Fetches full trade klines to compute MFE and MAE
  - Calls feature_store.update_outcome() so the ML training row is complete
  - Clears the dedup slot so the next valid setup fires immediately
  - Invalidates the performance cache to reflect updated win rates
  - Sends a Discord outcome notification
"""
import asyncio
import calendar
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import pandas as pd
from sqlmodel import Session, select

from app.models.db import Signal, engine
from app.engine.binance import get_klines
from app.engine.feature_store import update_outcome

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 120  # every 2 min — candle resolution doesn't need sub-minute checks

_HOURS_PER_BAR: dict[str, float] = {
    "1m": 1/60, "3m": 1/20, "5m": 1/12, "15m": 0.25, "30m": 0.5,
    "1h": 1.0, "2h": 2.0, "4h": 4.0, "6h": 6.0, "12h": 12.0, "1d": 24.0,
}

# v7 Task 1: Timeframe → seconds, used by the timestamp hard gate
_TF_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400,
}


async def _scan_klines_for_hit(sig: Signal) -> Tuple[Optional[str], Optional[float]]:
    """
    Walk each candle from signal creation to now looking for first TP1 or SL touch.

    Within a single candle we cannot know order, so we check SL first — the
    conservative (pessimistic) convention used in professional backtesting.

    Returns (result, exit_price) or (None, None) if neither level touched yet.
    """
    try:
        # created_at is a naive datetime stored in UTC. Python's .timestamp()
        # treats naive datetimes as local time — in UTC+7 this shifts 7 hours.
        # Use calendar.timegm() to convert correctly as UTC.
        sig_epoch = calendar.timegm(sig.created_at.timetuple())
        start_ms = sig_epoch * 1000
        klines = await get_klines(sig.symbol, sig.timeframe, limit=200, start_ms=start_ms)

        if klines is None or klines.empty:
            return None, None

        # v7 Task 1: Timestamp Hard Gate — look-ahead bias elimination.
        # The signal was generated from the CLOSE of candle T.  The first
        # candle we can legally trade is candle T+1, whose open time equals
        # the next candle-period boundary after signal creation.
        # We compute that boundary precisely so we never evaluate a candle
        # whose price action was already "baked into" the entry decision.
        tf_secs = _TF_SECONDS.get(sig.timeframe, 900)
        # Round UP to the start of the NEXT candle period
        next_candle_open = ((sig_epoch // tf_secs) + 1) * tf_secs
        hard_gate = pd.Timestamp(next_candle_open, unit="s")

        klines = klines[klines.index >= hard_gate]
        if klines.empty:
            return None, None

        for _, candle in klines.iterrows():
            lo = float(candle["low"])
            hi = float(candle["high"])

            if sig.direction == "LONG":
                if lo <= sig.sl:
                    return "loss", sig.sl
                if hi >= sig.tp1:
                    return "win", sig.tp1
            else:  # SHORT
                if hi >= sig.sl:
                    return "loss", sig.sl
                if lo <= sig.tp1:
                    return "win", sig.tp1

    except Exception as e:
        logger.warning(f"Kline scan failed for signal {sig.id}: {e}")

    return None, None


async def check_open_signals():
    """Label any open signals whose TP1 or SL has been touched in klines."""
    with Session(engine) as s:
        open_signals = s.exec(
            select(Signal).where(Signal.result == None)  # noqa: E711
        ).all()

    if not open_signals:
        return

    for sig in open_signals:
        try:
            age = datetime.utcnow() - sig.created_at
            if age > timedelta(hours=24):
                await _resolve(sig, "expired", None)
                continue

            # Skip signals younger than 2 candle periods — they can't have resolved yet
            min_age = timedelta(seconds=_TF_SECONDS.get(sig.timeframe, 900) * 2)
            if age < min_age:
                continue

            result, exit_price = await _scan_klines_for_hit(sig)
            if result:
                await _resolve(sig, result, exit_price)

        except Exception as e:
            logger.warning(f"Outcome check failed for signal {sig.id}: {e}")

        await asyncio.sleep(0.2)


async def _resolve(sig: Signal, result: str, exit_price: Optional[float]):
    """Mark signal resolved, enrich with trade stats, update feature store."""
    now = datetime.utcnow()
    duration_hours = (now - sig.created_at).total_seconds() / 3600

    # ── 1. Compute MFE / MAE from historical klines ──────────────────────────
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    actual_pnl_pct: Optional[float] = None

    if result in ("win", "loss") and exit_price is not None:
        try:
            hpb = _HOURS_PER_BAR.get(sig.timeframe, 1.0)
            bars_needed = min(500, max(5, math.ceil(duration_hours / hpb) + 10))
            sig_epoch_mfe = calendar.timegm(sig.created_at.timetuple())
            start_ms = sig_epoch_mfe * 1000

            klines = await get_klines(sig.symbol, sig.timeframe,
                                      limit=bars_needed, start_ms=start_ms)

            # v7 Task 1: same hard gate as _scan_klines_for_hit
            tf_secs = _TF_SECONDS.get(sig.timeframe, 900)
            sig_epoch = sig_epoch_mfe
            next_candle_open = ((sig_epoch // tf_secs) + 1) * tf_secs
            hard_gate = pd.Timestamp(next_candle_open, unit="s")
            klines = klines[klines.index >= hard_gate]

            if not klines.empty:
                if sig.direction == "LONG":
                    best_price = float(klines["high"].max())
                    worst_price = float(klines["low"].min())
                    mfe_pct = (best_price - sig.entry_price) / sig.entry_price * 100
                    mae_pct = (sig.entry_price - worst_price) / sig.entry_price * 100
                    actual_pnl_pct = (exit_price - sig.entry_price) / sig.entry_price * 100
                else:  # SHORT
                    best_price = float(klines["low"].min())
                    worst_price = float(klines["high"].max())
                    mfe_pct = (sig.entry_price - best_price) / sig.entry_price * 100
                    mae_pct = (worst_price - sig.entry_price) / sig.entry_price * 100
                    actual_pnl_pct = (sig.entry_price - exit_price) / sig.entry_price * 100
        except Exception as e:
            logger.warning(f"MFE/MAE computation failed for signal {sig.id}: {e}")

    # ── 2. Persist result ─────────────────────────────────────────────────────
    with Session(engine) as s:
        db_sig = s.get(Signal, sig.id)
        if not db_sig:
            return
        db_sig.result = result
        db_sig.result_at = now
        if exit_price is not None:
            db_sig.result_price = exit_price
        s.add(db_sig)
        s.commit()

    logger.info(
        f"Outcome {result.upper()}: {sig.symbol} {sig.direction} [{sig.timeframe}] "
        + (f"entry={sig.entry_price:.4f} exit={exit_price:.4f} "
           f"pnl={actual_pnl_pct:.2f}% mfe={mfe_pct:.2f}% mae={mae_pct:.2f}% "
           f"dur={duration_hours:.1f}h"
           if actual_pnl_pct is not None
           else f"dur={duration_hours:.1f}h")
    )

    # ── 3. Update ML feature store with outcome enrichment ────────────────────
    if result in ("win", "loss"):
        update_outcome(
            signal_id=sig.id,
            actual_pnl_pct=actual_pnl_pct,
            max_favorable_excursion=mfe_pct,
            max_adverse_excursion=mae_pct,
            time_to_result_hours=duration_hours,
        )

    # ── 4. Clear dedup slot & refresh performance cache ───────────────────────
    if result in ("win", "loss"):
        from app.engine.dedup import clear_symbol
        from app.engine.performance import invalidate
        clear_symbol(sig.symbol, sig.timeframe)
        invalidate(sig.symbol, sig.timeframe)

    # ── 5. Discord notification ───────────────────────────────────────────────
    if result in ("win", "loss"):
        asyncio.create_task(_notify_discord(sig, result, exit_price))


async def _notify_discord(sig: Signal, result: str, price: Optional[float]):
    try:
        from app.discord.bot import send_outcome_notification
        await send_outcome_notification(sig, result, price)
    except Exception as e:
        logger.warning(f"Discord outcome notification failed: {e}")


async def _run_outcome_loop():
    while True:
        try:
            await check_open_signals()
        except Exception as e:
            logger.error(f"Outcome tracker error: {e}")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


def start_outcome_tracker():
    asyncio.create_task(_run_outcome_loop())
    logger.info("Outcome tracker started (60 s interval, klines-based SL-first detection)")
