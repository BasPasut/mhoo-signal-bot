"""
Signal deduplication and state management layer.

Each (symbol, timeframe) slot tracks the last-sent signal in memory.
On process restart the state is re-hydrated from the last 4 hours of DB rows
so the first scan after a redeploy won't spam every coin again.

Dispatch rules (checked in order):
  1. No previous state            → "new"          (always send)
  2. Direction flipped + price moved ≥ HYSTERESIS  → "new"
  3. Direction flipped but price within hysteresis  → "blocked_flicker"
  4. Confidence tier upgraded (e.g. PRIME → ALPHA)  → "upgrade"
  5. Same hash (symbol+direction+tier unchanged)    → "blocked_duplicate"
  6. Price moved ≥ PRICE_DEVIATION_PCT from last    → "price_deviation"
  7. Cooldown window expired                        → "cooldown_expired"
  8. Default                                        → "blocked_cooldown"
"""
import hashlib
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

COOLDOWN_HOURS = 4
SYMBOL_COOLDOWN_HOURS = 2   # no same-symbol signal within 2h regardless of timeframe
PRICE_DEVIATION_PCT = 2.0   # re-send when price moves ≥ 2 % from last sent price
HYSTERESIS_PCT = 1.0        # direction flip only accepted when price moved ≥ 1 %

_TIER_ORDER = {"ALPHA": 2, "PRIME": 1, "SETUP": 0}

# In-memory state: "{symbol}:{timeframe}" → state dict
_state: dict[str, dict] = {}
# Symbol-level last-sent: symbol → datetime (cross-timeframe cooldown)
_symbol_sent: dict[str, datetime] = {}


# ── Public API ────────────────────────────────────────────────────────────────

def should_send_signal(signal: dict) -> tuple[bool, str]:
    """
    Decide whether to dispatch signal.
    Returns (send: bool, alert_type: str).
    alert_type values: "new" | "upgrade" | "price_deviation" | "cooldown_expired"
                       | "blocked_duplicate" | "blocked_cooldown" | "blocked_flicker"
    """
    symbol = signal["symbol"]
    tf = signal["timeframe"]
    direction = signal["direction"]
    confidence = signal["confidence"]
    price = signal["entry_price"]
    tier = _tier(confidence)
    state_key = f"{symbol}:{tf}"
    stored = _state.get(state_key)

    # ── 0. Symbol-level cross-timeframe cooldown ─────────────────────────────
    sym_last = _symbol_sent.get(symbol)
    if sym_last is not None:
        sym_elapsed = datetime.utcnow() - sym_last
        if sym_elapsed < timedelta(hours=SYMBOL_COOLDOWN_HOURS):
            remaining = int((timedelta(hours=SYMBOL_COOLDOWN_HOURS) - sym_elapsed).total_seconds() // 60)
            logger.debug(
                f"Dedup symbol-cooldown {symbol}/{tf}: {remaining}m until next allowed signal"
            )
            return False, "blocked_symbol_cooldown"

    # ── 1. First time we see this slot ───────────────────────────────────────
    if stored is None:
        _save(state_key, signal, tier, "new")
        return True, "new"

    stored_dir = stored["direction"]
    stored_tier = stored["tier"]
    stored_price = stored["last_price"]
    last_sent: datetime = stored["last_sent"]
    elapsed = datetime.utcnow() - last_sent
    price_dev = abs(price - stored_price) / stored_price * 100 if stored_price else 0.0
    new_hash = _hash(symbol, direction, tier)

    # ── 2 & 3. Direction flipped ─────────────────────────────────────────────
    if direction != stored_dir:
        if price_dev >= HYSTERESIS_PCT:
            _save(state_key, signal, tier, "new")
            return True, "new"
        logger.debug(
            f"Dedup flicker {symbol}/{tf}: direction {stored_dir}→{direction} "
            f"but price only moved {price_dev:.2f}% (need {HYSTERESIS_PCT}%)"
        )
        return False, "blocked_flicker"

    # ── 4. Tier upgraded ─────────────────────────────────────────────────────
    if _TIER_ORDER.get(tier, 0) > _TIER_ORDER.get(stored_tier, 0):
        _save(state_key, signal, tier, "upgrade")
        return True, "upgrade"

    # ── 5. Exact duplicate (same symbol+direction+tier, price within deviation) ──
    if new_hash == stored["hash"] and price_dev < PRICE_DEVIATION_PCT:
        logger.debug(
            f"Dedup duplicate {symbol}/{tf} {direction} tier={tier} "
            f"price_dev={price_dev:.2f}%"
        )
        return False, "blocked_duplicate"

    # ── 6. Significant price movement → new entry opportunity ────────────────
    if price_dev >= PRICE_DEVIATION_PCT:
        _save(state_key, signal, tier, "price_deviation")
        return True, "price_deviation"

    # ── 7. Cooldown window expired ───────────────────────────────────────────
    if elapsed >= timedelta(hours=COOLDOWN_HOURS):
        _save(state_key, signal, tier, "cooldown_expired")
        return True, "cooldown_expired"

    # ── 8. Within cooldown, nothing significant changed ──────────────────────
    remaining_min = max(0, (timedelta(hours=COOLDOWN_HOURS) - elapsed).seconds // 60)
    logger.debug(
        f"Dedup cooldown {symbol}/{tf} {direction}: "
        f"tier={tier} price_dev={price_dev:.2f}% "
        f"cooldown={remaining_min}m remaining"
    )
    return False, "blocked_cooldown"


def init_from_db():
    """
    Pre-populate in-memory state from DB rows of the last COOLDOWN_HOURS.
    Call once at startup so a redeploy doesn't re-send stale signals.
    """
    try:
        from sqlmodel import Session, select, desc
        from app.models.db import Signal, engine
        cutoff = datetime.utcnow() - timedelta(hours=COOLDOWN_HOURS)
        with Session(engine) as s:
            rows = s.exec(
                select(Signal)
                .where(Signal.created_at >= cutoff)
                .order_by(desc(Signal.created_at))
            ).all()
        # Only keep the most-recent row per (symbol, timeframe)
        seen: set[str] = set()
        seen_symbols: set[str] = set()
        for row in rows:
            key = f"{row.symbol}:{row.timeframe}"
            if key not in seen:
                seen.add(key)
                t = _tier(row.confidence)
                _state[key] = {
                    "hash": _hash(row.symbol, row.direction, t),
                    "last_sent": row.created_at,
                    "last_price": row.entry_price,
                    "tier": t,
                    "direction": row.direction,
                    "alert_type": "hydrated",
                }
            # Symbol-level cooldown: track the most-recent signal per symbol
            if row.symbol not in seen_symbols:
                seen_symbols.add(row.symbol)
                _symbol_sent[row.symbol] = row.created_at
        if seen:
            logger.info(f"Dedup state hydrated from DB: {len(seen)} slots")
    except Exception as e:
        logger.warning(f"Dedup init_from_db failed (non-fatal): {e}")


def clear_symbol(symbol: str, timeframe: str | None = None):
    """Remove dedup state for a coin (e.g., after SL confirmed hit)."""
    keys = [k for k in list(_state) if k.startswith(f"{symbol}:")]
    if timeframe:
        keys = [k for k in keys if k == f"{symbol}:{timeframe}"]
    for k in keys:
        del _state[k]
        logger.info(f"Dedup state cleared: {k}")


def get_state_summary() -> list[dict]:
    """Return human-readable snapshot of the dedup state (for the debug API)."""
    now = datetime.utcnow()
    out = []
    for key, v in _state.items():
        elapsed = now - v["last_sent"]
        cooldown_left = max(0.0, (timedelta(hours=COOLDOWN_HOURS) - elapsed).total_seconds() / 60)
        parts = key.split(":", 1)
        out.append({
            "symbol": parts[0],
            "timeframe": parts[1] if len(parts) > 1 else "—",
            "direction": v["direction"],
            "tier": v["tier"],
            "last_price": v["last_price"],
            "last_sent_utc": v["last_sent"].isoformat() + "Z",
            "elapsed_min": round(elapsed.total_seconds() / 60, 1),
            "cooldown_remaining_min": round(cooldown_left, 1),
            "alert_type": v.get("alert_type", "—"),
        })
    return sorted(out, key=lambda x: x["symbol"])


# ── Internals ─────────────────────────────────────────────────────────────────

def _tier(confidence: float) -> str:
    if confidence >= 80:
        return "ALPHA"
    if confidence >= 60:
        return "PRIME"
    return "SETUP"


def _hash(symbol: str, direction: str, tier: str) -> str:
    raw = f"{symbol}:{direction}:{tier}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _save(state_key: str, signal: dict, tier: str, alert_type: str):
    now = datetime.utcnow()
    _state[state_key] = {
        "hash": _hash(signal["symbol"], signal["direction"], tier),
        "last_sent": now,
        "last_price": signal["entry_price"],
        "tier": tier,
        "direction": signal["direction"],
        "alert_type": alert_type,
    }
    _symbol_sent[signal["symbol"]] = now
