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


def get_scan_interval() -> int:
    return int(get_config("scan_interval", str(settings.scan_interval_seconds)))
