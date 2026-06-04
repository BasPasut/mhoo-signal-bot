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
    _migrate(engine)


def _migrate(eng):
    from sqlalchemy import text, inspect
    with eng.connect() as conn:
        cols = [c["name"] for c in inspect(eng).get_columns("signal")]
        if "leverage" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN leverage INTEGER"))
            conn.commit()
        if "result_at" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN result_at DATETIME"))
            conn.commit()
        if "result_price" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN result_price FLOAT"))
            conn.commit()
        if "position_risk_pct" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN position_risk_pct FLOAT"))
            conn.commit()
        if "breakeven_trigger" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN breakeven_trigger FLOAT"))
            conn.commit()
        if "sl_method" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN sl_method VARCHAR"))
            conn.commit()
        if "tier" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN tier INTEGER"))
            conn.commit()
        if "tp1_hit" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN tp1_hit BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        if "tp1_hit_at" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN tp1_hit_at DATETIME"))
            conn.commit()
        if "breakeven_sl" not in cols:
            conn.execute(text("ALTER TABLE signal ADD COLUMN breakeven_sl FLOAT"))
            conn.commit()

        # trade_order table — auto-execution layer
        if "trade_order" not in inspect(eng).get_table_names():
            conn.execute(text("""
                CREATE TABLE trade_order (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    binance_order_id VARCHAR,
                    symbol VARCHAR NOT NULL,
                    binance_symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    order_type VARCHAR NOT NULL,
                    role VARCHAR NOT NULL,
                    quantity FLOAT NOT NULL,
                    price FLOAT NOT NULL DEFAULT 0.0,
                    stop_price FLOAT NOT NULL DEFAULT 0.0,
                    status VARCHAR NOT NULL DEFAULT 'NEW',
                    execution_mode VARCHAR NOT NULL,
                    error VARCHAR,
                    created_at DATETIME NOT NULL
                )
            """))
            conn.execute(text("CREATE INDEX ix_trade_order_signal_id ON trade_order (signal_id)"))
            conn.commit()


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

    leverage: Optional[int] = None
    position_risk_pct: Optional[float] = None
    breakeven_trigger: Optional[float] = None
    sl_method: Optional[str] = None    # "atr_1h" | "structural_15m"
    tier: Optional[int] = None         # 1 | 2 | 3 (liquidity tier)
    discord_sent: bool = False
    result: Optional[str] = None       # "win" | "loss" | "expired" | "breakeven" | None
    result_at: Optional[datetime] = None
    result_price: Optional[float] = None

    # TP1 hit / riding to TP2 state
    tp1_hit: bool = Field(default=False)
    tp1_hit_at: Optional[datetime] = None
    breakeven_sl: Optional[float] = None   # SL price after TP1 (entry ± fee buffer)

    @property
    def triggers(self) -> List[dict]:
        return json.loads(self.triggers_json)

    @triggers.setter
    def triggers(self, value: List[dict]):
        self.triggers_json = json.dumps(value)


class SignalFeatures(SQLModel, table=True):
    """
    Full indicator snapshot captured at signal creation time.
    Outcome columns (MFE, MAE, pnl, duration) are filled later by the outcome tracker.
    This table is the ML training dataset — one row per signal.
    """
    __tablename__ = "signal_features"

    id: Optional[int] = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="signal.id", unique=True, index=True)
    captured_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Price returns ─────────────────────────────────────────────────────────
    price: Optional[float] = None          # entry price (reference)
    price_change_1h: Optional[float] = None    # % return over last 1h
    price_change_4h: Optional[float] = None    # % return over last 4h
    price_change_24h: Optional[float] = None   # % return over last 24h

    # ── Regime / trend ────────────────────────────────────────────────────────
    regime: Optional[str] = None               # "bull" | "bear" | "sideways"
    ema9_gap: Optional[float] = None           # (price − EMA9) / EMA9 × 100
    ema21_gap: Optional[float] = None
    ema50_gap: Optional[float] = None
    ema200_gap: Optional[float] = None

    # ── Momentum ──────────────────────────────────────────────────────────────
    rsi_14: Optional[float] = None
    rsi_slope_3: Optional[float] = None        # RSI change over 3 bars
    macd_line: Optional[float] = None
    macd_signal_line: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_hist_slope: Optional[float] = None    # histogram change over 3 bars

    # ── Volatility ────────────────────────────────────────────────────────────
    bb_pct: Optional[float] = None             # position inside Bollinger Band [0,1]
    bb_width: Optional[float] = None           # band width / price
    atr_pct: Optional[float] = None            # ATR / price × 100

    # ── Trend strength ────────────────────────────────────────────────────────
    adx: Optional[float] = None
    adx_pos: Optional[float] = None            # +DI
    adx_neg: Optional[float] = None            # −DI

    # ── Volume ────────────────────────────────────────────────────────────────
    volume_ratio: Optional[float] = None       # current vol / 20-bar avg
    volume_trend_3: Optional[float] = None     # 3-bar % volume change

    # ── Candle structure (last closed bar) ───────────────────────────────────
    candle_body_pct: Optional[float] = None    # body / total range [0,1]
    candle_upper_shadow: Optional[float] = None
    candle_lower_shadow: Optional[float] = None

    # ── Market context ────────────────────────────────────────────────────────
    fear_greed: Optional[int] = None
    funding_rate: Optional[float] = None
    oi_change: Optional[float] = None

    # ── Timing ────────────────────────────────────────────────────────────────
    hour_utc: Optional[int] = None             # 0–23 (intraday session patterns)
    day_of_week: Optional[int] = None          # 0=Mon … 6=Sun

    # ── Outcome (filled by outcome_tracker on resolution) ────────────────────
    actual_pnl_pct: Optional[float] = None         # realised % P&L
    max_favorable_excursion: Optional[float] = None # max profit % before close
    max_adverse_excursion: Optional[float] = None   # max drawdown % before close
    time_to_result_hours: Optional[float] = None    # hours from entry to resolution


class Config(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True)
    value: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TradeOrder(SQLModel, table=True):
    __tablename__ = "trade_order"
    id: Optional[int] = Field(default=None, primary_key=True)
    signal_id: int = Field(index=True)
    binance_order_id: Optional[str] = None
    symbol: str
    binance_symbol: str
    side: str           # BUY / SELL
    order_type: str     # LIMIT / STOP_MARKET / TAKE_PROFIT
    role: str           # entry / sl / tp1 / tp2
    quantity: float
    price: float = 0.0
    stop_price: float = 0.0
    status: str = "NEW"
    execution_mode: str  # testnet / live
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
