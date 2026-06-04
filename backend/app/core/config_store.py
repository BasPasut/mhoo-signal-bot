from sqlmodel import Session, select
from app.models.db import Config, engine
from app.core.settings import settings
from datetime import datetime
from typing import Optional


def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    with Session(engine) as s:
        row = s.exec(select(Config).where(Config.key == key)).first()
        return row.value if row else default


def set_config(key: str, value: str):
    with Session(engine) as s:
        row = s.exec(select(Config).where(Config.key == key)).first()
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            row = Config(key=key, value=value)
        s.add(row)
        s.commit()


def get_watchlist() -> list[str]:
    raw = get_config("watchlist", settings.default_watchlist)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def set_watchlist(symbols: list[str]):
    set_config("watchlist", ",".join(s.upper() for s in symbols))


def get_risk_profile() -> str:
    return get_config("risk_profile", settings.default_risk_profile)


def set_risk_profile(profile: str):
    assert profile in ("conservative", "balanced", "aggressive")
    set_config("risk_profile", profile)


def get_timeframes() -> list[str]:
    raw = get_config("timeframes", settings.default_timeframes)
    return [t.strip() for t in raw.split(",") if t.strip()]


def set_timeframes(timeframes: list[str]):
    set_config("timeframes", ",".join(t.strip() for t in timeframes if t.strip()))


def get_scan_interval() -> int:
    return int(get_config("scan_interval", str(settings.scan_interval_seconds)))


_VALID_PRIORITY_BIASES = ("Highest Confidence", "Lowest Risk")
_VALID_SIGNAL_TIERS    = {"ALPHA", "PRIME", "SETUP"}


def get_signal_tiers() -> list[str]:
    raw = get_config("signal_tiers", "ALPHA,PRIME,SETUP")
    return [t.strip() for t in raw.split(",") if t.strip() in _VALID_SIGNAL_TIERS]


def set_signal_tiers(tiers: list[str]):
    valid = [t for t in tiers if t in _VALID_SIGNAL_TIERS]
    assert valid, "At least one valid tier required"
    set_config("signal_tiers", ",".join(valid))


def get_max_open_positions() -> int:
    """Maximum concurrent open signals (correlation guard)."""
    # fall back to old key if present (migration)
    val = get_config("max_open_positions") or get_config("max_signals_per_cycle", "5")
    return int(val)


def set_max_open_positions(n: int):
    assert 1 <= n <= 10, "max_open_positions must be 1–10"
    set_config("max_open_positions", str(n))


def get_priority_bias() -> str:
    return get_config("priority_bias", "Highest Confidence")


def set_priority_bias(bias: str):
    assert bias in _VALID_PRIORITY_BIASES, f"priority_bias must be one of {_VALID_PRIORITY_BIASES}"
    set_config("priority_bias", bias)


_VALID_EXECUTION_MODES = {"disabled", "testnet", "live"}


def get_execution_mode() -> str:
    return get_config("execution_mode", "disabled")


def set_execution_mode(mode: str):
    assert mode in _VALID_EXECUTION_MODES
    set_config("execution_mode", mode)


def get_starting_balance() -> float:
    return float(get_config("starting_balance", "10000"))


def set_starting_balance(balance: float):
    assert balance >= 1, "starting_balance must be >= 1"
    set_config("starting_balance", str(balance))


# Risk % per signal tier (fixed, replaces Kelly)
_DEFAULT_RISK_PER_TIER = {"ALPHA": 1.5, "PRIME": 1.0, "SETUP": 0.5}


def get_risk_per_tier() -> dict[str, float]:
    raw = get_config("risk_per_tier", "")
    if not raw:
        return dict(_DEFAULT_RISK_PER_TIER)
    try:
        parsed = {}
        for part in raw.split(","):
            k, v = part.strip().split(":")
            parsed[k.strip()] = float(v.strip())
        return parsed
    except Exception:
        return dict(_DEFAULT_RISK_PER_TIER)


def set_risk_per_tier(tiers: dict[str, float]):
    for k in ("ALPHA", "PRIME", "SETUP"):
        assert k in tiers, f"Missing tier: {k}"
        assert 0.1 <= tiers[k] <= 10.0, f"{k} risk must be 0.1–10%"
    raw = ",".join(f"{k}:{tiers[k]}" for k in ("ALPHA", "PRIME", "SETUP"))
    set_config("risk_per_tier", raw)
