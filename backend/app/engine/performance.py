"""
Historical performance tracker.

Tracks per-(symbol, timeframe) win rates from resolved signals and
provides a confidence adjustment that feeds back into the scorer.

The feedback loop:
  resolve signal → store result → refresh cache → next scan reads adjusted confidence
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory cache: (symbol, timeframe) → stats dict
_cache: dict[tuple, dict] = {}
_CACHE_TTL = 300  # seconds — refresh at most every 5 minutes


def _stale(entry: dict) -> bool:
    return (datetime.utcnow() - entry["updated_at"]).total_seconds() > _CACHE_TTL


def _fetch(symbol: str, timeframe: str) -> dict:
    """Query DB for all resolved signals for this symbol+timeframe."""
    try:
        from sqlmodel import Session, select
        from app.models.db import Signal, engine
        with Session(engine) as s:
            rows = s.exec(
                select(Signal)
                .where(Signal.symbol == symbol)
                .where(Signal.timeframe == timeframe)
                .where(Signal.result.in_(["win", "loss"]))  # type: ignore
            ).all()
        wins = sum(1 for r in rows if r.result == "win")
        n = len(rows)
        recent = [r for r in rows if r.result_at and
                  r.result_at >= datetime.utcnow() - timedelta(days=30)]
        recent_wins = sum(1 for r in recent if r.result == "win")
        recent_n = len(recent)
        return {
            "wins": wins,
            "losses": n - wins,
            "n": n,
            "wr": wins / n if n > 0 else 0.0,
            "recent_n": recent_n,
            "recent_wr": recent_wins / recent_n if recent_n > 0 else 0.0,
            "updated_at": datetime.utcnow(),
        }
    except Exception as e:
        logger.warning(f"performance._fetch failed for {symbol}/{timeframe}: {e}")
        return {"wins": 0, "losses": 0, "n": 0, "wr": 0.0,
                "recent_n": 0, "recent_wr": 0.0, "updated_at": datetime.utcnow()}


def get_stats(symbol: str, timeframe: str) -> dict:
    key = (symbol.upper(), timeframe)
    if key not in _cache or _stale(_cache[key]):
        _cache[key] = _fetch(symbol.upper(), timeframe)
    return _cache[key]


def get_win_rate(symbol: str, timeframe: str, min_trades: int = 10) -> Optional[float]:
    """Return historical win rate [0–1] if we have enough data, else None."""
    stats = get_stats(symbol, timeframe)
    if stats["n"] < min_trades:
        return None
    # Prefer the 30-day recent window if it has enough data
    if stats["recent_n"] >= min_trades:
        return stats["recent_wr"]
    return stats["wr"]


def confidence_adjustment(symbol: str, timeframe: str) -> float:
    """
    Return confidence point adjustment based on historical performance.

    Baseline expectation: 60% WR.
    Each 5% above/below baseline → ±2.5 confidence points.
    Range: -10 to +10 points. Requires ≥10 resolved trades.

    Examples:
      80% WR (+20pp) → +10 pts   (strong track record)
      70% WR (+10pp) → +5 pts
      60% WR (0pp)   → 0 pts     (meeting expectations)
      50% WR (-10pp) → -5 pts
      40% WR (-20pp) → -10 pts   (underperforming — throttle signals)
    """
    wr = get_win_rate(symbol, timeframe)
    if wr is None:
        return 0.0
    baseline = 0.60
    delta_pp = (wr - baseline) * 100        # percentage points above/below baseline
    adj = delta_pp * 0.5                    # 0.5 confidence points per pp
    capped = max(-10.0, min(10.0, adj))
    logger.debug(
        f"Perf adj {symbol}/{timeframe}: WR={wr*100:.1f}% "
        f"→ {capped:+.1f} conf pts"
    )
    return capped


def invalidate(symbol: str, timeframe: str):
    """Force cache refresh for this slot (call after a signal resolves)."""
    key = (symbol.upper(), timeframe)
    _cache.pop(key, None)


def equity_curve(starting_balance: float = 10_000.0) -> list[dict]:
    """
    Compute a hypothetical paper-trading equity curve from all resolved signals.

    Each resolved signal is treated as a paper trade:
      WIN  → gains  risk_reward × risk_usd
      LOSS → loses  risk_usd  (1R)

    Returns list of data points ordered by resolution time — ready to feed a chart.
    """
    try:
        from sqlmodel import Session, select
        from sqlalchemy import asc
        from app.models.db import Signal, engine
        with Session(engine) as s:
            resolved = s.exec(
                select(Signal)
                .where(Signal.result.in_(["win", "loss"]))  # type: ignore
                .order_by(asc(Signal.result_at))
            ).all()
    except Exception as e:
        logger.error(f"equity_curve query failed: {e}")
        return []

    if not resolved:
        return []

    balance = starting_balance
    peak = starting_balance
    curve = []

    for sig in resolved:
        risk_pct = sig.position_risk_pct or 1.25
        risk_usd = balance * (risk_pct / 100)

        if sig.result == "win":
            pnl = risk_usd * (sig.risk_reward or 1.5)
        else:
            pnl = -risk_usd

        balance = max(0.0, balance + pnl)
        peak = max(peak, balance)
        drawdown_pct = (peak - balance) / peak * 100 if peak > 0 else 0.0

        curve.append({
            "date": sig.result_at.isoformat() + "Z",
            "balance": round(balance, 2),
            "pnl": round(pnl, 2),
            "result": sig.result,
            "symbol": sig.symbol,
            "timeframe": sig.timeframe,
            "direction": sig.direction,
            "confidence": round(sig.confidence, 1),
            "drawdown_pct": round(drawdown_pct, 2),
        })

    return curve


def portfolio_summary(starting_balance: float = 10_000.0) -> dict:
    """Aggregate metrics derived from the equity curve."""
    curve = equity_curve(starting_balance)

    empty = {
        "starting_balance": starting_balance,
        "final_balance": starting_balance,
        "total_return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "profit_factor": None,
        "sharpe_ratio": None,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "current_streak": 0,
        "current_streak_type": None,
    }

    if not curve:
        return empty

    final = curve[-1]["balance"]
    total_return_pct = (final - starting_balance) / starting_balance * 100
    max_dd = max(c["drawdown_pct"] for c in curve)

    wins_list = [c for c in curve if c["result"] == "win"]
    losses_list = [c for c in curve if c["result"] == "loss"]
    gross_profit = sum(c["pnl"] for c in wins_list)
    gross_loss = abs(sum(c["pnl"] for c in losses_list))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    # Per-trade Sharpe (no risk-free rate; meaningful at ≥20 trades)
    sharpe: float | None = None
    if len(curve) >= 5:
        import statistics
        prev_bal = starting_balance
        pnl_pcts = []
        for c in curve:
            if prev_bal > 0:
                pnl_pcts.append(c["pnl"] / prev_bal * 100)
            prev_bal = c["balance"]
        if len(pnl_pcts) > 1:
            std_r = statistics.stdev(pnl_pcts)
            if std_r > 0:
                sharpe = round(statistics.mean(pnl_pcts) / std_r, 3)

    # Current streak
    streak = 0
    streak_type: str | None = None
    for c in reversed(curve):
        if streak_type is None:
            streak_type = c["result"]
        if c["result"] == streak_type:
            streak += 1
        else:
            break

    return {
        "starting_balance": starting_balance,
        "final_balance": round(final, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe,
        "total_trades": len(curve),
        "wins": len(wins_list),
        "losses": len(losses_list),
        "current_streak": streak,
        "current_streak_type": streak_type,
    }


def get_all_stats() -> list[dict]:
    """Return performance breakdown for all symbol/timeframe pairs."""
    try:
        from sqlmodel import Session, select
        from app.models.db import Signal, engine
        with Session(engine) as s:
            rows = s.exec(
                select(Signal)
                .where(Signal.result.in_(["win", "loss", "expired"]))  # type: ignore
            ).all()
    except Exception as e:
        logger.error(f"get_all_stats failed: {e}")
        return []

    buckets: dict[tuple, dict] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "expired": 0, "durations": []}
    )
    for sig in rows:
        key = (sig.symbol, sig.timeframe)
        if sig.result == "win":
            buckets[key]["wins"] += 1
        elif sig.result == "loss":
            buckets[key]["losses"] += 1
        else:
            buckets[key]["expired"] += 1
        # Track resolution duration
        if sig.result_at and sig.created_at:
            hours = (sig.result_at - sig.created_at).total_seconds() / 3600
            buckets[key]["durations"].append(round(hours, 1))

    result = []
    for (symbol, tf), data in sorted(buckets.items()):
        decided = data["wins"] + data["losses"]
        avg_dur = (sum(data["durations"]) / len(data["durations"])
                   if data["durations"] else None)
        result.append({
            "symbol": symbol,
            "timeframe": tf,
            "wins": data["wins"],
            "losses": data["losses"],
            "expired": data["expired"],
            "total": decided + data["expired"],
            "win_rate": round(data["wins"] / decided * 100, 1) if decided else None,
            "avg_duration_hours": round(avg_dur, 1) if avg_dur is not None else None,
        })
    return result
