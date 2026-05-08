from sqlmodel import SQLModel, Field, create_engine, Session
from typing import Optional, List
from datetime import datetime
from app.core.settings import settings
import json

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


class Signal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    symbol: str          # e.g. "BTC"
    direction: str       # "LONG" | "SHORT"
    timeframe: str       # e.g. "15m"
    risk_profile: str    # "conservative" | "balanced" | "aggressive"

    entry_price: float
    entry_low: float     # entry zone low
    entry_high: float    # entry zone high
    tp1: float
    tp2: float
    sl: float
    risk_reward: float

    confidence: float    # 0–100
    ta_score: float      # layer scores
    pattern_score: float
    ml_score: float
    context_score: float

    # JSON-encoded list of triggered signal labels
    triggers_json: str = Field(default="[]")

    rsi: Optional[float] = None
    macd_hist: Optional[float] = None
    volume_ratio: Optional[float] = None
    funding_rate: Optional[float] = None
    fear_greed: Optional[int] = None

    discord_sent: bool = False
    result: Optional[str] = None  # "win" | "loss" | "partial" | None

    @property
    def triggers(self) -> List[dict]:
        return json.loads(self.triggers_json)

    @triggers.setter
    def triggers(self, value: List[dict]):
        self.triggers_json = json.dumps(value)


class Config(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True)
    value: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)
