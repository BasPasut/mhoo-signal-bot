"""
Outcome tracker — periodically checks open signals against klines data.

Two-phase resolution:

  Phase 1  (tp1_hit=False)
    Walk candles from signal creation.  SL-first within each candle.
    • SL touched first  → result = "loss"
    • TP1 touched first → mark tp1_hit=True, set breakeven_sl, continue to Phase 2.
      If TP2 is also touched in the same or a later candle in the same pass → "win".

  Phase 2  (tp1_hit=True)
    Walk candles from tp1_hit_at.  breakeven_sl-first within each candle.
    • breakeven_sl touched → result = "breakeven"   (no loss, SL moved to entry)
    • TP2 touched first   → result = "win"

TP2 validity is established automatically: if price never reaches TP2 within 24h of
the TP1 hit, the signal expires ("expired").  If TP2 is already past when TP1 fires,
the kline scanner detects the touch in the same pass and closes immediately as "win".

Fee buffer (0.05%) is added to the breakeven SL so the trade at worst covers round-trip fees.

On resolution:
  - Stores result_at, result_price
  - Fetches full trade klines to compute MFE and MAE
  - Calls feature_store.update_outcome() so ML training data is complete
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

_CHECK_INTERVAL_SECONDS = 120  # every 2 min

_HOURS_PER_BAR: dict[str, float] = {
    "1m": 1/60, "3m": 1/20, "5m": 1/12, "15m": 0.25, "30m": 0.5,
    "1h": 1.0, "2h": 2.0, "4h": 4.0, "6h": 6.0, "12h": 12.0, "1d": 24.0,
}

_TF_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400,
}

# 0.05% fee buffer — ensures breakeven_sl covers round-trip taker fees
_FEE_BUFFER = 0.0005


def _calc_breakeven_sl(sig: Signal) -> float:
    if sig.direction == "LONG":
        return round(sig.entry_price * (1 + _FEE_BUFFER), 8)
    return round(sig.entry_price * (1 - _FEE_BUFFER), 8)


def _get_hard_gate(sig_epoch: int, tf_secs: int) -> pd.Timestamp:
    """Round up to the start of the next candle period after signal creation."""
    next_candle_open = ((sig_epoch // tf_secs) + 1) * tf_secs
    return pd.Timestamp(next_candle_open, unit="s")


async def _phase1_scan(sig: Signal) -> Tuple[Optional[str], Optional[float], Optional[pd.Timestamp]]:
    """
    Phase 1: scan from signal creation for SL or TP1.

    Returns:
        ("loss",  sl_price,  None)              — SL hit
        ("tp1",   tp1_price, tp1_candle_time)   — TP1 hit, continue to phase 2
        ("win",   tp2_price, tp1_candle_time)   — TP1 and TP2 both hit in same pass
        (None,    None,      None)              — neither level touched yet
    """
    try:
        sig_epoch = calendar.timegm(sig.created_at.timetuple())
        start_ms = sig_epoch * 1000
        klines = await get_klines(sig.symbol, sig.timeframe, limit=200, start_ms=start_ms)
        if klines is None or klines.empty:
            return None, None, None

        tf_secs = _TF_SECONDS.get(sig.timeframe, 900)
        hard_gate = _get_hard_gate(sig_epoch, tf_secs)
        klines = klines[klines.index >= hard_gate]
        if klines.empty:
            return None, None, None

        tp1_hit_candle: Optional[pd.Timestamp] = None

        for ts, candle in klines.iterrows():
            lo = float(candle["low"])
            hi = float(candle["high"])

            if tp1_hit_candle is None:
                # Still looking for first TP1 or SL touch
                if sig.direction == "LONG":
                    if lo <= sig.sl:
                        return "loss", sig.sl, None
                    if hi >= sig.tp1:
                        tp1_hit_candle = ts
                        # Check TP2 in the same candle
                        if sig.tp2 > 0 and hi >= sig.tp2:
                            return "win", sig.tp2, tp1_hit_candle
                else:  # SHORT
                    if hi >= sig.sl:
                        return "loss", sig.sl, None
                    if lo <= sig.tp1:
                        tp1_hit_candle = ts
                        if sig.tp2 > 0 and lo <= sig.tp2:
                            return "win", sig.tp2, tp1_hit_candle
            else:
                # TP1 already hit — look for TP2 in subsequent candles
                if sig.tp2 > 0:
                    if sig.direction == "LONG" and hi >= sig.tp2:
                        return "win", sig.tp2, tp1_hit_candle
                    if sig.direction == "SHORT" and lo <= sig.tp2:
                        return "win", sig.tp2, tp1_hit_candle
                else:
                    # No TP2 defined — TP1 hit counts as win
                    return "win", sig.tp1, tp1_hit_candle

        # Did we at least hit TP1 in this batch of klines?
        if tp1_hit_candle is not None:
            return "tp1", sig.tp1, tp1_hit_candle

    except Exception as e:
        logger.warning(f"Phase1 scan failed for signal {sig.id}: {e}")

    return None, None, None


async def _phase2_scan(sig: Signal) -> Tuple[Optional[str], Optional[float]]:
    """
    Phase 2: scan from tp1_hit_at for breakeven_sl or TP2.

    Returns:
        ("breakeven", breakeven_price) — SL at breakeven triggered
        ("win",       tp2_price)       — TP2 reached
        (None, None)                   — still riding
    """
    try:
        if sig.tp1_hit_at is None:
            return None, None

        tp1_epoch = calendar.timegm(sig.tp1_hit_at.timetuple())
        start_ms = tp1_epoch * 1000
        klines = await get_klines(sig.symbol, sig.timeframe, limit=200, start_ms=start_ms)
        if klines is None or klines.empty:
            return None, None

        be_sl = sig.breakeven_sl or _calc_breakeven_sl(sig)
        tp2   = sig.tp2

        for _, candle in klines.iterrows():
            lo = float(candle["low"])
            hi = float(candle["high"])

            if sig.direction == "LONG":
                # Breakeven SL-first (conservative)
                if lo <= be_sl:
                    return "breakeven", be_sl
                if tp2 > 0 and hi >= tp2:
                    return "win", tp2
            else:  # SHORT
                if hi >= be_sl:
                    return "breakeven", be_sl
                if tp2 > 0 and lo <= tp2:
                    return "win", tp2

    except Exception as e:
        logger.warning(f"Phase2 scan failed for signal {sig.id}: {e}")

    return None, None


async def _mark_tp1_hit(sig: Signal, tp1_candle_time: pd.Timestamp):
    """Set tp1_hit=True and compute the breakeven SL. Sends a Discord notification."""
    now = datetime.utcnow()
    be_sl = _calc_breakeven_sl(sig)

    with Session(engine) as s:
        db_sig = s.get(Signal, sig.id)
        if not db_sig or db_sig.tp1_hit:
            return
        db_sig.tp1_hit = True
        db_sig.tp1_hit_at = now
        db_sig.breakeven_sl = be_sl
        s.add(db_sig)
        s.commit()

    logger.info(
        f"TP1 HIT: {sig.symbol} {sig.direction} [{sig.timeframe}] "
        f"tp1={sig.tp1:.4f} breakeven_sl={be_sl:.4f} — riding to TP2={sig.tp2:.4f}"
    )

    # Push real-time update to all connected dashboard clients
    from app.core.ws import manager
    asyncio.create_task(manager.broadcast_tp1_update(
        signal_id=sig.id,
        breakeven_sl=be_sl,
        tp1_hit_at=now.isoformat() + "Z",
    ))
    asyncio.create_task(_notify_discord_tp1(sig, be_sl))
    asyncio.create_task(_move_binance_sl_to_breakeven(sig, be_sl))


async def check_open_signals():
    """Check all open signals.  Routes Phase-1 vs Phase-2 based on tp1_hit flag."""
    with Session(engine) as s:
        open_signals = s.exec(
            select(Signal).where(Signal.result == None)  # noqa: E711
        ).all()

    if not open_signals:
        return

    for sig in open_signals:
        try:
            age = datetime.utcnow() - sig.created_at

            if not sig.tp1_hit:
                # ── Phase 1 ──────────────────────────────────────────────────
                if age > timedelta(hours=24):
                    await _resolve(sig, "expired", None)
                    continue

                min_age = timedelta(seconds=_TF_SECONDS.get(sig.timeframe, 900) * 2)
                if age < min_age:
                    continue

                result, exit_price, tp1_ts = await _phase1_scan(sig)

                if result == "tp1":
                    await _mark_tp1_hit(sig, tp1_ts)
                elif result in ("win", "loss"):
                    await _resolve(sig, result, exit_price)

            else:
                # ── Phase 2 ──────────────────────────────────────────────────
                # 24h timeout from tp1_hit_at
                riding_age = datetime.utcnow() - (sig.tp1_hit_at or sig.created_at)
                if riding_age > timedelta(hours=24):
                    await _resolve(sig, "expired", None)
                    continue

                result, exit_price = await _phase2_scan(sig)

                if result in ("win", "breakeven"):
                    await _resolve(sig, result, exit_price)

        except Exception as e:
            logger.warning(f"Outcome check failed for signal {sig.id}: {e}")

        await asyncio.sleep(0.2)


async def _resolve(sig: Signal, result: str, exit_price: Optional[float]):
    """Mark signal resolved, enrich with trade stats, update feature store."""
    now = datetime.utcnow()
    duration_hours = (now - sig.created_at).total_seconds() / 3600

    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    actual_pnl_pct: Optional[float] = None

    if result in ("win", "loss", "breakeven") and exit_price is not None:
        try:
            hpb = _HOURS_PER_BAR.get(sig.timeframe, 1.0)
            bars_needed = min(500, max(5, math.ceil(duration_hours / hpb) + 10))
            sig_epoch = calendar.timegm(sig.created_at.timetuple())
            start_ms = sig_epoch * 1000

            klines = await get_klines(sig.symbol, sig.timeframe,
                                      limit=bars_needed, start_ms=start_ms)

            tf_secs = _TF_SECONDS.get(sig.timeframe, 900)
            hard_gate = _get_hard_gate(sig_epoch, tf_secs)
            klines = klines[klines.index >= hard_gate]

            if not klines.empty:
                if sig.direction == "LONG":
                    best_price  = float(klines["high"].max())
                    worst_price = float(klines["low"].min())
                    mfe_pct = (best_price - sig.entry_price) / sig.entry_price * 100
                    mae_pct = (sig.entry_price - worst_price) / sig.entry_price * 100
                    actual_pnl_pct = (exit_price - sig.entry_price) / sig.entry_price * 100
                else:
                    best_price  = float(klines["low"].min())
                    worst_price = float(klines["high"].max())
                    mfe_pct = (sig.entry_price - best_price) / sig.entry_price * 100
                    mae_pct = (worst_price - sig.entry_price) / sig.entry_price * 100
                    actual_pnl_pct = (sig.entry_price - exit_price) / sig.entry_price * 100
        except Exception as e:
            logger.warning(f"MFE/MAE computation failed for signal {sig.id}: {e}")

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

    if result in ("win", "loss", "breakeven"):
        update_outcome(
            signal_id=sig.id,
            actual_pnl_pct=actual_pnl_pct,
            max_favorable_excursion=mfe_pct,
            max_adverse_excursion=mae_pct,
            time_to_result_hours=duration_hours,
        )

    if result in ("win", "loss", "breakeven"):
        from app.engine.dedup import clear_symbol
        from app.engine.performance import invalidate
        clear_symbol(sig.symbol, sig.timeframe)
        invalidate(sig.symbol, sig.timeframe)

    # Cancel any lingering open orders (LIMIT entry if expired before fill)
    asyncio.create_task(_cancel_binance_orders(sig.id, result))

    # Close the position on Binance via MARKET order so the exchange position
    # reflects the resolved outcome (works on testnet where conditional orders
    # are not supported, and as a safety net on live Binance)
    asyncio.create_task(_close_binance_position(sig.id, sig.symbol, sig.direction, result))

    asyncio.create_task(_notify_discord(sig, result, exit_price))


async def _close_binance_position(signal_id: int, symbol: str, direction: str, result: str):
    try:
        from app.engine.execution import close_position_market
        await close_position_market(signal_id, symbol, direction)
    except Exception as e:
        logger.warning(f"[resolution] close_position_market failed for signal {signal_id}: {e}")


async def _cancel_binance_orders(signal_id: int, result: str):
    try:
        from app.engine.execution import cancel_signal_orders
        await cancel_signal_orders(signal_id)
    except Exception as e:
        logger.warning(f"[resolution] cancel_signal_orders failed for signal {signal_id}: {e}")


async def _move_binance_sl_to_breakeven(sig: Signal, breakeven_sl: float):
    try:
        from app.engine.execution import move_sl_to_breakeven
        ok = await move_sl_to_breakeven(sig.id, sig.symbol, breakeven_sl, sig.direction)
        if ok:
            logger.info(f"[tp1] Binance SL moved to breakeven {breakeven_sl:.4f} for signal {sig.id}")
        else:
            logger.warning(f"[tp1] Binance SL move failed for signal {sig.id} — manual intervention needed")
    except Exception as e:
        logger.warning(f"[tp1] move_sl_to_breakeven exception for signal {sig.id}: {e}")


async def _notify_discord(sig: Signal, result: str, price: Optional[float]):
    try:
        from app.discord.bot import send_outcome_notification
        await send_outcome_notification(sig, result, price)
    except Exception as e:
        logger.warning(f"Discord outcome notification failed: {e}")


async def _notify_discord_tp1(sig: Signal, breakeven_sl: float):
    try:
        from app.discord.bot import send_tp1_notification
        await send_tp1_notification(sig, breakeven_sl)
    except Exception as e:
        logger.warning(f"Discord TP1 notification failed: {e}")


async def _run_outcome_loop():
    while True:
        try:
            await check_open_signals()
        except Exception as e:
            logger.error(f"Outcome tracker error: {e}")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


def start_outcome_tracker():
    asyncio.create_task(_run_outcome_loop())
    logger.info("Outcome tracker started (120 s interval, 2-phase klines resolution)")
