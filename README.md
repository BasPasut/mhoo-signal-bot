# Mhoo Signal Bot

High-accuracy crypto futures signal bot for Binance USDT-M perpetuals. Sends rich signals to Discord and displays a live dashboard with real-time PnL tracking.

## Architecture

```
Binance Futures API + News APIs
          ↓
Python (FastAPI + Signal Engine + Discord bot)   ← Railway / VPS
          ↓ REST + WebSocket
Next.js Dashboard                                 ← Vercel / local
          ↓
Discord Server                                    ← your server
```

**2 services. No Redis. No MongoDB. No message queue.**

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12 + FastAPI |
| Database | SQLite + SQLModel |
| Scheduler | APScheduler (in-process) |
| Frontend | Next.js 14 + TailwindCSS + Recharts |
| Discord | discord.py |
| Hosting | Railway (backend) + Vercel (frontend) |

---

## Signal Algorithm

Signals are scored across 4 layers into a final confidence score (0–100):

| Layer | Weight | What it checks |
|---|---|---|
| Technical indicators | 75% | RSI, MACD, Bollinger Bands, EMA alignment, ADX, Stochastic — multi-timeframe confluence (MTF) |
| Chart patterns | 15% | Engulfing, hammer, S/R breakout, trend structure |
| ML model (XGBoost) | 5% | Trained online on resolved signal outcomes |
| Market context | 5% | Funding rate, Fear & Greed Index, news sentiment |

On top of the base score, the **Smart Money Concepts (SMC)** layer adds up to +0.30:

| SMC Signal | Bonus |
|---|---|
| Order Block hit in direction | +0.10 – +0.15 |
| Fair Value Gap in direction | +0.08 |
| Liquidity sweep (stop hunt before reversal) | +0.20 |

Signal fires when confidence ≥ threshold (per risk profile):

| Profile | Threshold |
|---|---|
| Conservative | 80% |
| Balanced (default) | 68% |
| Aggressive | 50% |

---

## Risk Management

### Liquidity Tiers

| Tier | Symbols | Max Leverage | Position Budget |
|---|---|---|---|
| Tier 1 | BTC, ETH | 20x | 10% equity |
| Tier 2 | BNB, SOL, XRP, ADA, AVAX, LINK, ARB, OP, SUI, INJ, and ~15 more | 10x | 10% equity |
| Tier 3 | Everything else (meme coins, micro-caps) | 5x | 7.5% equity |

### Stop Loss Logic

```
SL primary   = vol_tier × 1H ATR
SL surgical  = nearest 15m fractal swing  (used when ATR-SL > 2.5% of price)
SL noise     = max(structural, N × ATR_15m)
               └─ Tier 1: 1.5×   Tier 2/3: 2.0×  (anti stop-hunt buffer)
```

### Take Profit Logic

```
TP1 = max(1.0× ATR, 1.5× SL_dist)   → guaranteed R/R ≥ 1:1.5
TP2 = TP1 + 1.0× ATR                 → runner extension
On TP1 hit → SL moves to entry + fees (breakeven)
```

Leverage is calculated as `floor(position_budget / sl_pct)`, minimum 2x. Signals below 2x leverage are suppressed.

---

## Signal Deduplication

Each `(symbol, timeframe)` slot tracks the last-sent signal in memory and re-hydrates from DB on restart (no spam after redeploy).

A signal is sent when any of these conditions are met:

| Condition | Action |
|---|---|
| No previous state | Send (new) |
| Direction flipped + price moved ≥ 1% | Send (new) |
| Confidence tier upgraded (SETUP → PRIME → ALPHA) | Send (upgrade) |
| Price moved ≥ 2% from last sent price | Send (price deviation) |
| Cooldown window (4h) expired | Send (cooldown expired) |
| Direction flipped but price within 1% | Block (flicker) |
| Same hash, within cooldown | Block (duplicate) |

Cross-symbol cooldown: same symbol cannot fire again within 2 hours regardless of timeframe.

---

## Outcome Tracking & ML Feedback Loop

The outcome tracker runs every 2 minutes and resolves open signals against kline OHLC data using a **2-phase approach**:

### Phase 1 — Watching for SL or TP1
Walk candles from signal creation. SL-first within each candle (conservative / realistic).
- **SL touched** → `result = loss`
- **TP1 touched** → enter Phase 2. If TP2 is also hit in the same kline pass → `result = win` immediately.

### Phase 2 — Riding to TP2 (risk-free)
Walk candles from `tp1_hit_at`. Breakeven SL-first.
- **Breakeven SL touched** → `result = breakeven` (no loss, SL was at entry + fees)
- **TP2 touched** → `result = win`
- **24h timeout** → `result = expired`

On any resolution:
- Stores `result_at`, `result_price`, MFE, MAE
- Updates ML feature store so XGBoost retrains on real outcomes
- Clears dedup slot so the next valid setup fires immediately
- Sends Discord notification (WIN / BREAKEVEN / LOSS / EXPIRED)

### Position Limit Exemption
Once a signal hits TP1, it is marked `tp1_hit=True` and **no longer counted** against the max open positions limit. A new signal can enter immediately. Signals in Phase 2 are shown in a separate "🔒 Riding to TP2" section on the dashboard.

---

## Manual Overrides

Each open signal card has an override panel (expand ▾):
- **Leverage** — preset buttons capped by liquidity tier + custom input
- **TP1 / TP2** — number inputs with live R/R and % gain preview; validated for correct direction
- Changes persist to DB immediately

---

## Dashboard Features

- **Live signals** — real-time via WebSocket; moves to "Riding" section instantly when TP1 hits
- **Riding to TP2 section** — separate yellow-bordered section; shows breakeven SL, TP2 as active target, riding duration
- **Live PnL** — per open position, fetched from Binance on load + every 60s
- **Stats bar** — win rate, W/L/BE counts, open + riding positions, total signals
- **Analytics panel** — confidence distribution, win rate by direction, tier breakdown
- **Performance page** — equity curve, MFE/MAE distribution, per-symbol stats
- **History page** — full signal log with filters; WIN / BREAKEVEN / STOPPED OUT / EXPIRED banners
- **Settings page** — live config: watchlist, risk profile, timeframes, scan interval, max positions

---

## Quick Start

```bash
git clone https://github.com/BasPasut/mhoo-signal-bot
cd mhoo-signal-bot

# Backend
cd backend
cp ../.env.example .env        # fill in keys (see Environment Variables below)
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                    # runs on http://localhost:3000
```

---

## Environment Variables

Copy `.env.example` to `backend/.env` and fill in:

```env
# Required
DISCORD_BOT_TOKEN=        # from Discord Developer Portal
DISCORD_CHANNEL_ID=       # right-click channel → Copy ID
DISCORD_GUILD_ID=         # right-click server → Copy ID

# Optional — needed only for order execution
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Optional — testnet execution
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=

# Optional — news sentiment scoring
CRYPTOPANIC_API_KEY=      # free tier available at cryptopanic.com

# Defaults (override as needed)
DEFAULT_WATCHLIST=BTC,ETH,BNB,SOL,XRP,ADA,AVAX,DOT,LINK,TON,TRX,DOGE
DEFAULT_RISK_PROFILE=balanced
DEFAULT_TIMEFRAMES=15m,1h
SCAN_INTERVAL_SECONDS=300
```

Frontend only needs one variable:

```env
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Project Structure

```
backend/
  app/
    api/routes.py              # REST + WebSocket endpoints
    core/
      settings.py              # env-based config (pydantic-settings)
      config_store.py          # runtime config (watchlist, risk profile, etc.)
      ws.py                    # WebSocket connection manager
    discord/bot.py             # Discord bot — signal embeds + outcome notifications
    engine/
      binance.py               # Binance Futures API client (klines, price, funding rate)
      scorer.py                # Main scoring pipeline (v8-Ultimate)
      dedup.py                 # Signal deduplication + cooldown state
      execution.py             # Order placement (market / limit)
      feature_store.py         # ML training data store (SQLite-backed)
      signal_queue.py          # Async signal dispatch queue
      performance.py           # Confidence adjustment from historical win rates
      indicators/
        technical.py           # RSI, MACD, BB, EMA, ADX, Stochastic
        smc.py                 # Order Blocks, FVGs, Liquidity Sweeps
      patterns/chart.py        # Candlestick + S/R pattern detection
      ml/model.py              # XGBoost model — online training + inference
    models/db.py               # SQLModel ORM — Signal, TradeOrder tables
    scheduler/
      runner.py                # APScheduler — scan loop + interval control
      outcome_tracker.py       # Resolves open signals against klines (WIN/LOSS/EXPIRED)
    main.py                    # FastAPI app + lifespan startup

frontend/
  src/
    app/
      page.tsx                 # Main dashboard (signals + live PnL)
      history/page.tsx         # Signal history with filters
      performance/page.tsx     # Equity curve + performance stats
      settings/page.tsx        # Live config editor
    components/
      signals/
        SignalCard.tsx          # Per-signal card with live price + PnL
        StatsBar.tsx            # Win rate, R/R, open positions summary
        AnalyticsPanel.tsx      # Charts: confidence dist, win by direction
      layout/Nav.tsx            # Top navigation
    hooks/useWebSocket.ts       # WebSocket hook for real-time signal push
    lib/api.ts                  # Typed API client
```

---

## Watchlist

Edit via the Settings page in the dashboard, or set `DEFAULT_WATCHLIST` in `.env` before starting. Any Binance USDT-M perpetual pair works: `BTC,ETH,XRP,SOL,BNB,DOGE,ADA,ARB,SUI`
