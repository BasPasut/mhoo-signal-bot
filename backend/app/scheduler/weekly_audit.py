"""
Weekly performance audit + win-rate watchdog.

Two jobs:
  weekly_audit  — runs every 7 days (Monday 00:05 UTC).
                  Posts a full performance breakdown to Discord and
                  updates the auto-exclusion list for underperforming symbols.

  wr_watchdog   — runs every scan cycle (after each scan completes).
                  Posts a Discord alert if the rolling 7-day WR drops below
                  WR_ALERT_THRESHOLD. Fires at most once per 6 hours to avoid spam.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

WR_ALERT_THRESHOLD   = 0.55    # alert if 7-day WR drops below 55%
WR_EXCLUSION_MIN     = 0.40    # auto-exclude symbol if WR < 40%
EXCLUSION_MIN_TRADES = 8       # need at least this many resolved trades to exclude
_WATCHDOG_COOLDOWN_H = 12      # hours between repeated watchdog alerts
# Cooldown timestamp is persisted in the Config table (key below) so it survives
# BE restarts — an in-memory global reset on every redeploy, causing alert spam.
_WATCHDOG_STATE_KEY  = "wr_watchdog_last_alert"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_recent_signals(days: int) -> list:
    try:
        from sqlmodel import Session, select
        from app.models.db import Signal, engine
        cutoff = datetime.utcnow() - timedelta(days=days)
        with Session(engine) as s:
            return s.exec(
                select(Signal)
                .where(Signal.result.in_(["win", "loss", "breakeven"]))
                .where(Signal.result_at >= cutoff)
            ).all()
    except Exception as e:
        logger.warning(f"weekly_audit._get_recent_signals failed: {e}")
        return []


def _wr(signals: list) -> Optional[float]:
    decided = [s for s in signals if s.result in ("win", "loss")]
    if not decided:
        return None
    return sum(1 for s in decided if s.result == "win") / len(decided)


def _symbol_breakdown(signals: list) -> list[dict]:
    """Per-symbol WR breakdown, sorted by signal count desc."""
    from collections import defaultdict
    buckets: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "breakevens": 0})
    for s in signals:
        sym = s.symbol
        if s.result == "win":
            buckets[sym]["wins"] += 1
        elif s.result == "loss":
            buckets[sym]["losses"] += 1
        else:
            buckets[sym]["breakevens"] += 1
    out = []
    for sym, d in buckets.items():
        decided = d["wins"] + d["losses"]
        out.append({
            "symbol": sym,
            "wins": d["wins"],
            "losses": d["losses"],
            "breakevens": d["breakevens"],
            "decided": decided,
            "wr": d["wins"] / decided if decided else None,
        })
    return sorted(out, key=lambda x: x["decided"], reverse=True)


def _conf_bucket_breakdown(signals: list) -> list[dict]:
    buckets = [
        ("73–80", 73, 80),
        ("80–90", 80, 90),
        ("90+",   90, 101),
    ]
    rows = []
    for label, lo, hi in buckets:
        subset = [s for s in signals if lo <= s.confidence < hi and s.result in ("win", "loss")]
        if not subset:
            continue
        wins = sum(1 for s in subset if s.result == "win")
        rows.append({"label": label, "wins": wins, "losses": len(subset) - wins,
                     "wr": wins / len(subset)})
    return rows


def _direction_breakdown(signals: list) -> dict:
    decided = [s for s in signals if s.result in ("win", "loss")]
    longs  = [s for s in decided if s.direction == "LONG"]
    shorts = [s for s in decided if s.direction == "SHORT"]
    return {
        "long_wr":   _wr(longs),
        "long_n":    len(longs),
        "short_wr":  _wr(shorts),
        "short_n":   len(shorts),
    }


# ── Auto-exclusion ────────────────────────────────────────────────────────────

def run_auto_exclusion(signals_7d: list) -> tuple[list[str], list[str]]:
    """
    Check per-symbol WR across ALL history (not just 7d) and update the
    auto-exclusion list.  Returns (newly_excluded, newly_cleared).
    """
    from app.core.config_store import get_excluded_symbols, set_excluded_symbols
    from sqlmodel import Session, select
    from app.models.db import Signal, engine

    try:
        # Pull all history per symbol
        with Session(engine) as s:
            all_resolved = s.exec(
                select(Signal)
                .where(Signal.result.in_(["win", "loss"]))
            ).all()
    except Exception:
        return [], []

    from collections import defaultdict
    sym_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})
    for sig in all_resolved:
        sym_stats[sig.symbol]["wins" if sig.result == "win" else "losses"] += 1

    current_excluded = set(get_excluded_symbols())
    newly_excluded: list[str] = []
    newly_cleared: list[str] = []

    for sym, d in sym_stats.items():
        decided = d["wins"] + d["losses"]
        if decided < EXCLUSION_MIN_TRADES:
            continue
        wr = d["wins"] / decided
        if wr < WR_EXCLUSION_MIN and sym not in current_excluded:
            current_excluded.add(sym)
            newly_excluded.append(sym)
            logger.warning(
                f"[audit] Auto-excluding {sym}: WR={wr*100:.0f}% "
                f"({d['wins']}W/{d['losses']}L over {decided} signals)"
            )
        elif wr >= WR_EXCLUSION_MIN + 0.10 and sym in current_excluded:
            # Recovered: WR climbed back above 50% — reinstate
            current_excluded.discard(sym)
            newly_cleared.append(sym)
            logger.info(f"[audit] Reinstating {sym}: WR recovered to {wr*100:.0f}%")

    set_excluded_symbols(list(current_excluded))
    return newly_excluded, newly_cleared


# ── Weekly audit ──────────────────────────────────────────────────────────────

async def run_weekly_audit():
    """Full 7-day performance review — posts to Discord and updates exclusions."""
    logger.info("[weekly_audit] Running weekly performance audit...")

    signals_7d  = _get_recent_signals(7)
    signals_30d = _get_recent_signals(30)

    if not signals_7d:
        logger.info("[weekly_audit] No resolved signals in the last 7 days — skipping")
        return

    # Overall stats
    decided_7d = [s for s in signals_7d if s.result in ("win", "loss")]
    wins_7d    = sum(1 for s in decided_7d if s.result == "win")
    wr_7d      = wins_7d / len(decided_7d) if decided_7d else None

    decided_30d = [s for s in signals_30d if s.result in ("win", "loss")]
    wins_30d    = sum(1 for s in decided_30d if s.result == "win")
    wr_30d      = wins_30d / len(decided_30d) if decided_30d else None

    sym_breakdown = _symbol_breakdown(signals_7d)
    conf_breakdown = _conf_bucket_breakdown(signals_7d)
    dir_breakdown  = _direction_breakdown(signals_7d)

    # Portfolio balance delta
    from app.engine.performance import portfolio_summary
    summary = portfolio_summary()

    # Auto-exclusion
    newly_excluded, newly_cleared = run_auto_exclusion(signals_7d)
    from app.core.config_store import get_excluded_symbols
    all_excluded = get_excluded_symbols()

    # Post to Discord
    try:
        from app.discord.bot import send_weekly_report
        await send_weekly_report(
            signals_7d=len(decided_7d),
            wr_7d=wr_7d,
            wins_7d=wins_7d,
            losses_7d=len(decided_7d) - wins_7d,
            signals_30d=len(decided_30d),
            wr_30d=wr_30d,
            summary=summary,
            sym_breakdown=sym_breakdown,
            conf_breakdown=conf_breakdown,
            dir_breakdown=dir_breakdown,
            newly_excluded=newly_excluded,
            newly_cleared=newly_cleared,
            all_excluded=all_excluded,
        )
    except Exception as e:
        logger.warning(f"[weekly_audit] Discord send failed: {e}")

    logger.info(
        f"[weekly_audit] Done — 7d: {wins_7d}W/{len(decided_7d)-wins_7d}L "
        f"WR={wr_7d*100:.1f}% | excluded: {all_excluded}"
    )


# ── Win-rate watchdog ─────────────────────────────────────────────────────────

def _get_last_watchdog_alert() -> Optional[datetime]:
    """Read the persisted last-alert timestamp (survives restarts)."""
    try:
        from app.core.config_store import get_config
        raw = get_config(_WATCHDOG_STATE_KEY)
        return datetime.fromisoformat(raw) if raw else None
    except Exception:
        return None


def _set_last_watchdog_alert(ts: datetime):
    try:
        from app.core.config_store import set_config
        set_config(_WATCHDOG_STATE_KEY, ts.isoformat())
    except Exception as e:
        logger.warning(f"[watchdog] failed to persist cooldown timestamp: {e}")


async def run_wr_watchdog():
    """
    Check rolling 7-day WR after every scan. Alert on Discord if it drops
    below WR_ALERT_THRESHOLD. Fires at most once per WATCHDOG_COOLDOWN_H hours.

    The cooldown timestamp is persisted in the Config table so it is NOT reset
    by BE restarts/redeploys (the previous in-memory global caused alert spam,
    re-firing on every restart while the 7-day WR was below threshold).
    """
    last_alert = _get_last_watchdog_alert()
    if last_alert is not None:
        elapsed = datetime.utcnow() - last_alert
        if elapsed < timedelta(hours=_WATCHDOG_COOLDOWN_H):
            return

    signals_7d = _get_recent_signals(7)
    decided    = [s for s in signals_7d if s.result in ("win", "loss")]
    if len(decided) < 5:
        return  # not enough data

    wr = sum(1 for s in decided if s.result == "win") / len(decided)
    if wr >= WR_ALERT_THRESHOLD:
        return

    wins   = sum(1 for s in decided if s.result == "win")
    losses = len(decided) - wins
    logger.warning(
        f"[watchdog] 7-day WR={wr*100:.1f}% below {WR_ALERT_THRESHOLD*100:.0f}% "
        f"({wins}W/{losses}L over {len(decided)} signals)"
    )
    _set_last_watchdog_alert(datetime.utcnow())

    try:
        from app.discord.bot import send_watchdog_alert
        await send_watchdog_alert(wr=wr, wins=wins, losses=losses, n=len(decided))
    except Exception as e:
        logger.warning(f"[watchdog] Discord send failed: {e}")
