"""
Signal Priority Queue  —  v7-Ultimate
======================================
Sorts and caps candidate signals from a single scan cycle.

Sort keys:
  "Highest Confidence" (default):
      1. confidence descending
      2. tier ascending  (Tier 1 > 2 > 3 on a tie)
      3. sl_pct ascending  (tighter stop as secondary tiebreaker)

  "Lowest Risk":
      1. sl_pct ascending  (smallest SL% = lowest per-trade risk)
      2. tier ascending
      3. confidence descending
"""
from __future__ import annotations
import logging
from typing import Literal

logger = logging.getLogger(__name__)

PriorityBias = Literal["Highest Confidence", "Lowest Risk"]

# Lower number = higher queue priority
_TIER_RANK: dict[int, int] = {1: 0, 2: 1, 3: 2}


def _sl_pct(signal: dict) -> float:
    """Stop-loss distance as a fraction of entry price."""
    entry = signal.get("entry_price", 0.0)
    sl    = signal.get("sl", 0.0)
    if entry <= 0:
        return 1.0
    return abs(entry - sl) / entry


def prioritize_signals(
    signals:       list[dict],
    max_signals:   int = 5,
    priority_bias: PriorityBias = "Highest Confidence",
) -> list[dict]:
    """
    Sort `signals` by the chosen bias and return the top `max_signals`.

    Args:
        signals:       flat list of signal dicts (all directions, all symbols)
        max_signals:   maximum signals to return from this cycle
        priority_bias: sort strategy

    Returns:
        Ordered list of the highest-priority signals (len ≤ max_signals).
    """
    if not signals:
        return []

    # Minimum net-of-fee R/R. TP1 = max(ATR, 1.5×SL) guarantees ~1.36–1.47 net
    # after 0.08% round-trip fee; 1.5 gross is never achievable, so threshold is 1.3.
    _RR_MIN = 1.3
    below_rr = [s["symbol"] for s in signals if s.get("risk_reward", 0) < _RR_MIN]
    if below_rr:
        logger.info(
            f"R/R filter: {len(below_rr)} candidate(s) dropped (R/R < {_RR_MIN}): {below_rr}"
        )
    signals = [s for s in signals if s.get("risk_reward", 0) >= _RR_MIN]
    if not signals:
        return []

    if priority_bias == "Lowest Risk":
        ranked = sorted(
            signals,
            key=lambda s: (
                _sl_pct(s),
                _TIER_RANK.get(s.get("tier", 3), 2),
                -s.get("confidence", 0),
            ),
        )
    else:  # "Highest Confidence"
        ranked = sorted(
            signals,
            key=lambda s: (
                -s.get("confidence", 0),
                _TIER_RANK.get(s.get("tier", 3), 2),
                _sl_pct(s),
            ),
        )

    selected = ranked[:max_signals]
    n_dropped = len(signals) - len(selected)

    if n_dropped > 0:
        dropped_syms = [s["symbol"] for s in ranked[max_signals:]]
        logger.info(
            f"Priority queue [{priority_bias}]: "
            f"{len(signals)} candidates → {len(selected)} queued, "
            f"dropped: {dropped_syms}"
        )

    return selected


def explain_queue(
    signals:       list[dict],
    max_signals:   int = 5,
    priority_bias: PriorityBias = "Highest Confidence",
) -> str:
    """Return a human-readable breakdown of queue decisions (used in stress tests)."""
    if not signals:
        return "  (empty candidate list)"

    selected_set = {id(s) for s in prioritize_signals(signals, max_signals, priority_bias)}
    lines = [
        f"  Priority Queue  |  bias='{priority_bias}'  |  "
        f"limit={max_signals}  |  candidates={len(signals)}",
        f"  {'Rank':>4}  {'Symbol':<8} {'T':>1}  {'Conf%':>6}  {'SL%':>6}  {'Decision'}",
        f"  {'-'*54}",
    ]

    if priority_bias == "Lowest Risk":
        ranked = sorted(
            signals,
            key=lambda s: (
                _sl_pct(s),
                _TIER_RANK.get(s.get("tier", 3), 2),
                -s.get("confidence", 0),
            ),
        )
    else:
        ranked = sorted(
            signals,
            key=lambda s: (
                -s.get("confidence", 0),
                _TIER_RANK.get(s.get("tier", 3), 2),
                _sl_pct(s),
            ),
        )

    for rank, sig in enumerate(ranked, 1):
        sl = _sl_pct(sig) * 100
        decision = "QUEUED ✓" if rank <= max_signals else "dropped"
        lines.append(
            f"  {rank:>4}  {sig['symbol']:<8} {sig.get('tier', '?'):>1}"
            f"  {sig.get('confidence', 0):>5.1f}%"
            f"  {sl:>5.2f}%  {decision}"
        )

    queued_ct  = min(max_signals, len(ranked))
    dropped_ct = max(0, len(ranked) - max_signals)
    lines.append(f"  {'-'*54}")
    lines.append(f"  Result: {queued_ct} queued, {dropped_ct} dropped")
    return "\n".join(lines)
