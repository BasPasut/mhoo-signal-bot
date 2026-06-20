import asyncio
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from app.engine.scorer import score_symbol
from app.engine.dedup import should_send_signal, init_from_db
from app.engine.feature_store import save_features
from app.core.config_store import (
    get_watchlist, get_risk_profile, get_scan_interval,
    get_max_open_positions, get_priority_bias, get_signal_tiers,
    get_excluded_symbols,
)
from app.engine.signal_queue import prioritize_signals
from app.core.ws import manager
from app.discord.bot import send_signal
from app.models.db import Signal, engine
from app.core.version import ALGO_VERSION

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _execute_signal(signal: dict, signal_id: int):
    try:
        from app.engine.execution import place_signal_orders
        await place_signal_orders(signal, signal_id)
    except Exception as e:
        logger.error(f"[execution] Failed for signal {signal_id}: {e}", exc_info=True)

# Rate limiter: 4 concurrent symbol scans — enough throughput but avoids
# bursting 8 heavy pandas TA computations simultaneously
_SCAN_SEMAPHORE = asyncio.Semaphore(4)

# Global scan lock — prevents run_now() from launching a concurrent scan while the
# scheduled scan is still in flight (would cause dedup bypasses and duplicate signals)
_SCAN_LOCK = asyncio.Lock()


def _open_signal_count() -> int:
    """Count open positions that still carry full risk.
    Signals that have hit TP1 (tp1_hit=True) have SL at breakeven — they cannot
    lose money and therefore do NOT count against the position limit."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    with Session(engine) as s:
        rows = s.exec(
            select(Signal)
            .where(Signal.result == None)  # noqa: E711
            .where(Signal.tp1_hit == False)  # noqa: E712
            .where(Signal.created_at >= cutoff)
        ).all()
        return len(rows)


async def _run_scan():
    if _SCAN_LOCK.locked():
        logger.info("Scan skipped — previous scan still in progress")
        return
    async with _SCAN_LOCK:
        await _run_scan_inner()


async def _run_scan_inner():
    watchlist = get_watchlist()
    excluded = set(get_excluded_symbols())
    if excluded:
        before = len(watchlist)
        watchlist = [s for s in watchlist if s not in excluded]
        logger.info(f"Exclusion filter: removed {before - len(watchlist)} symbols ({', '.join(excluded)})")
    risk_profile = get_risk_profile()
    max_open = get_max_open_positions()
    priority_bias = get_priority_bias()

    logger.info(f"Scan starting: {len(watchlist)} symbols, MTF 4H→1H→15m+1h [{risk_profile}]")
    await manager.broadcast_status(f"Scanning {len(watchlist)} symbols...")

    # ── Phase 1: Scan — 15m and 1h entry TF per symbol ───────────────────────
    async def _scan_symbol(symbol: str) -> tuple[str, list[dict]]:
        async with _SCAN_SEMAPHORE:
            signals = []
            for tf in ("15m", "1h"):
                try:
                    sig = await score_symbol(symbol, tf, risk_profile)
                    if sig is not None:
                        signals.append(sig)
                except Exception as e:
                    logger.error(f"Error scanning {symbol}/{tf}: {e}", exc_info=True)
            return symbol, signals

    tasks = [_scan_symbol(sym) for sym in watchlist]
    scan_results = await asyncio.gather(*tasks)

    # ── Phase 2: Filter → build candidate list ────────────────────────────────
    candidates: list[dict] = []
    _RR_MIN = 1.5  # raised from 1.3 → all signals were clustering at 1.30-1.46 with negative EV
    allowed_tiers = get_signal_tiers()

    for symbol, signals in scan_results:
        for signal in signals:
            # R/R pre-check — skip before touching dedup state so failed signals
            # don't ghost-block the slot for future scans
            if signal.get("risk_reward", 0) < _RR_MIN:
                logger.info(
                    f"R/R pre-filter: {symbol} R/R={signal.get('risk_reward', 0):.2f} < {_RR_MIN} — skipped"
                )
                continue

            # Signal tier filter — only dispatch tiers the user has selected
            conf = signal.get("confidence", 0)
            grade = "ALPHA" if conf >= 80 else "PRIME" if conf >= 60 else "SETUP"
            if grade not in allowed_tiers:
                logger.info(
                    f"Tier filter: {symbol} {grade} ({conf:.0f}%) not in {allowed_tiers} — skipped"
                )
                continue

            # Smart dedup: hash + cooldown + tier-upgrade + price deviation
            send, alert_type = should_send_signal(signal)
            if not send:
                logger.info(
                    f"Dedup [{alert_type}] {symbol} {signal['direction']} "
                    f"conf={signal['confidence']:.0f}%"
                )
                continue

            signal["alert_type"] = alert_type
            candidates.append(signal)

    # ── Phase 3: Sort candidates by priority (no per-cycle cap) ─────────────
    queued = prioritize_signals(candidates, len(candidates), priority_bias)
    if candidates:
        logger.info(
            f"Priority queue [{priority_bias}]: "
            f"{len(candidates)} candidates sorted, dispatching all"
        )

    # ── Phase 4: Dispatch selected signals ────────────────────────────────────
    for signal in queued:
        symbol = signal["symbol"]
        direction = signal["direction"]

        # Correlation guard: don't pile on if too many positions already open
        open_count = _open_signal_count()
        if open_count >= max_open:
            logger.info(
                f"Correlation guard: {open_count}/{max_open} open signals, skipping {symbol}"
            )
            continue

        # Extract ML feature snapshot before saving (not a DB column)
        features_snapshot = signal.pop("_features", None)

        try:
            row = Signal(
                symbol=signal["symbol"],
                direction=signal["direction"],
                timeframe=signal["timeframe"],
                risk_profile=signal["risk_profile"],
                entry_price=signal["entry_price"],
                entry_low=signal["entry_low"],
                entry_high=signal["entry_high"],
                tp1=signal["tp1"],
                tp2=signal["tp2"],
                sl=signal["sl"],
                risk_reward=signal["risk_reward"],
                confidence=signal["confidence"],
                ta_score=signal["ta_score"],
                pattern_score=signal["pattern_score"],
                ml_score=signal["ml_score"],
                context_score=signal["context_score"],
                leverage=signal.get("leverage"),
                position_risk_pct=signal.get("position_risk_pct"),
                breakeven_trigger=signal.get("breakeven_trigger"),
                sl_method=signal.get("sl_method"),
                tier=signal.get("tier"),
                rsi=signal["meta"].get("rsi"),
                macd_hist=signal["meta"].get("macd_hist"),
                volume_ratio=signal["meta"].get("volume_ratio"),
                funding_rate=signal["meta"].get("funding_rate"),
                fear_greed=signal["meta"].get("fear_greed_value"),
                algo_version=ALGO_VERSION,
                market_regime=signal.get("regime"),
            )
            row.triggers = signal["triggers"]

            with Session(engine) as s:
                s.add(row)
                s.commit()
                s.refresh(row)
                signal["id"] = row.id

            # Fire-and-forget — don't await, don't block signal dispatch
            asyncio.create_task(_execute_signal(signal, row.id))

            # Save ML feature snapshot linked to the new signal id
            if features_snapshot is not None:
                save_features(row.id, features_snapshot)

            # Flatten meta fields so the WebSocket payload matches the REST API shape
            meta = signal.get("meta", {})
            ws_payload = {
                k: v for k, v in signal.items()
                if k not in ("meta", "_features", "regime", "trailing_stop_atr")
            }
            ws_payload["rsi"] = meta.get("rsi")
            ws_payload["volume_ratio"] = meta.get("volume_ratio")
            ws_payload["funding_rate"] = meta.get("funding_rate")
            ws_payload["fear_greed"] = meta.get("fear_greed_value")
            ws_payload["discord_sent"] = False
            ws_payload["result"] = None
            ws_payload["result_at"] = None
            ws_payload["result_price"] = None

            await manager.broadcast_signal(ws_payload)
            await send_signal(signal)

            row.discord_sent = True
            with Session(engine) as s:
                s.add(row)
                s.commit()

            logger.info(
                f"Signal: {symbol} {signal['direction']} "
                f"{signal['confidence']:.0f}% confidence [MTF]"
            )

        except Exception as e:
            logger.error(f"Error persisting {symbol}: {e}", exc_info=True)

    await manager.broadcast_status("Scan complete")

    try:
        from app.scheduler.weekly_audit import run_wr_watchdog
        await run_wr_watchdog()
    except Exception as e:
        logger.warning(f"[watchdog] Failed: {e}")


def start_scheduler():
    init_from_db()  # pre-populate dedup state so restart doesn't re-send stale signals
    interval = get_scan_interval()
    scheduler.add_job(
        _run_scan,
        trigger=IntervalTrigger(seconds=interval),
        id="signal_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started — scanning every {interval}s")


def update_interval(seconds: int):
    scheduler.reschedule_job(
        "signal_scan",
        trigger=IntervalTrigger(seconds=seconds),
    )


async def run_now():
    """Trigger an immediate scan outside the schedule."""
    asyncio.create_task(_run_scan())
