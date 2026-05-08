import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session
from datetime import datetime

from app.engine.scorer import score_symbol
from app.core.config_store import get_watchlist, get_timeframes, get_risk_profile, get_scan_interval
from app.core.ws import manager
from app.discord.bot import send_signal
from app.models.db import Signal, engine

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _run_scan():
    watchlist = get_watchlist()
    timeframes = get_timeframes()
    risk_profile = get_risk_profile()

    logger.info(f"Scan starting: {watchlist} on {timeframes} [{risk_profile}]")
    await manager.broadcast_status(f"Scanning {len(watchlist)} symbols...")

    for symbol in watchlist:
        for tf in timeframes:
            try:
                signal = await score_symbol(symbol, tf, risk_profile)
                if signal is None:
                    continue

                # Persist to DB
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
                    rsi=signal["meta"].get("rsi"),
                    macd_hist=signal["meta"].get("macd_hist"),
                    volume_ratio=signal["meta"].get("volume_ratio"),
                    funding_rate=signal["meta"].get("funding_rate"),
                    fear_greed=signal["meta"].get("fear_greed_value"),
                )
                row.triggers = signal["triggers"]

                with Session(engine) as s:
                    s.add(row)
                    s.commit()
                    s.refresh(row)
                    signal["id"] = row.id

                # Push to dashboard
                await manager.broadcast_signal(signal)

                # Send Discord
                await send_signal(signal)
                row.discord_sent = True
                with Session(engine) as s:
                    s.add(row)
                    s.commit()

                logger.info(
                    f"Signal: {symbol} {signal['direction']} "
                    f"{signal['confidence']:.0f}% confidence [{tf}]"
                )

            except Exception as e:
                logger.error(f"Error scanning {symbol}/{tf}: {e}", exc_info=True)

            await asyncio.sleep(0.5)  # gentle rate limiting

    await manager.broadcast_status("Scan complete")


def start_scheduler():
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
