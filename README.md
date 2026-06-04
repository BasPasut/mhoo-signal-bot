# Mhoo Signal Bot

High-accuracy crypto futures Mhoo signal bot. Sends signals to Discord and displays a live dashboard.

## Architecture

```
Binance + News APIs
       ↓
Python (FastAPI + Signal Engine + Discord bot)   ← Railway
       ↓ REST + WebSocket
Next.js Dashboard                                 ← Vercel
       ↓
Discord Server                                    ← your server
```

**2 services. 1 language. No Redis. No MongoDB. No Go.**

## Stack

| Layer | Tech | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI | One process: API + engine + Discord bot |
| Database | SQLite + SQLModel | Zero infra, file on disk, handles millions of rows |
| Scheduler | APScheduler | In-process cron, no queue needed |
| Frontend | Next.js 14 + TailwindCSS | SSR for mobile speed, free on Vercel |
| Charts | Recharts | Lightweight, composable |
| Discord | discord.py | Rich embeds, slash commands |
| Hosting | Railway (backend) + Vercel (frontend) | Railway never sleeps on free tier |

## Quick Start

```bash
git clone https://github.com/BasPasut/binance-signal-bot
cd binance-signal-bot

# Backend
cd backend
cp ../.env.example .env   # fill in DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
cp ../.env.example .env.local   # set NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000

## Environment Variables

See `.env.example` for all variables with descriptions.

Required to start:
- `DISCORD_BOT_TOKEN` — from Discord Developer Portal
- `DISCORD_CHANNEL_ID` — right-click channel → Copy ID

Optional:
- `BINANCE_API_KEY` / `BINANCE_API_SECRET` — only if you want account data (not needed for signals)
- `CRYPTOPANIC_API_KEY` — for news sentiment (free tier available)

## Signal Algorithm

Signals combine 4 layers, weighted into a final confidence score (0–100):

| Layer | Weight | What it checks |
|---|---|---|
| Technical indicators | 40% | RSI, MACD, Bollinger Bands, EMA alignment, ADX, Stochastic |
| Chart patterns | 25% | Engulfing, hammer, S/R breakout, trend structure |
| ML model (XGBoost) | 25% | Trained on historical Binance futures data |
| Market context | 10% | Fear & Greed Index, funding rate, news sentiment |

Signal fires when confidence ≥ threshold (configurable per risk profile):
- Conservative: 80%
- Balanced: 70% (default)
- Aggressive: 60%

## Deployment

See [docs/deployment.md](docs/deployment.md).

## Watchlist

Edit via the Settings page in the dashboard, or set `DEFAULT_WATCHLIST` in `.env` before starting.
Any Binance USDT-M perpetual pair works: `BTC,ETH,XRP,SOL,BNB,DOGE,ADA`
