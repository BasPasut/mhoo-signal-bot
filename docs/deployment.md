# Deployment Guide

## Stack
- **Backend** → [Fly.io](https://fly.io) — free tier (shared-cpu-1x, 256MB RAM, persistent volume)
- **Frontend** → [Vercel](https://vercel.com) — free tier (Next.js native)

---

## Prerequisites

```bash
# Install Fly CLI
# Windows (PowerShell)
iwr https://fly.io/install.ps1 -useb | iex

# macOS / Linux
curl -L https://fly.io/install.sh | sh

# Install Vercel CLI (optional — can also deploy via GitHub integration)
npm i -g vercel
```

---

## Backend — Fly.io

### 1. Login and create app

```bash
cd backend
fly auth login
fly launch --no-deploy --name mhoo-signal-bot --region sin
# YES to using the existing fly.toml
# NO to PostgreSQL
# NO to Redis
```

### 2. Create the persistent volume

```bash
# 1 GB is plenty for SQLite + ML models + history CSVs
fly volumes create mhoo_data --region sin --size 1
```

### 3. Set secrets

```bash
fly secrets set \
  DISCORD_BOT_TOKEN="your_token" \
  DISCORD_CHANNEL_ID="your_channel_id" \
  DISCORD_GUILD_ID="your_guild_id" \
  BINANCE_API_KEY="your_key" \
  BINANCE_API_SECRET="your_secret" \
  SECRET_KEY="change_me_use_random_hex" \
  CORS_ORIGINS="https://mhoo-signal-bot.vercel.app" \
  CRYPTOPANIC_API_KEY="optional"
```

> `DATABASE_URL`, `ML_MODELS_DIR`, `ML_HISTORY_DIR` are already set in `fly.toml`.

### 4. Deploy

```bash
fly deploy
```

### 5. Verify

```bash
fly logs
fly status
curl https://mhoo-signal-bot.fly.dev/api/health
```

### Subsequent deploys

```bash
cd backend && fly deploy
```

### Useful commands

```bash
fly ssh console        # SSH into running machine
fly volumes list       # Check volume status
fly secrets list       # List secret keys (values hidden)
fly logs -f            # Stream live logs
```

---

## Frontend — Vercel

### Option A: GitHub Integration (recommended)

1. [vercel.com](https://vercel.com) → New Project → Import `BasPasut/mhoo-signal-bot`
2. Set **Root Directory** = `frontend`
3. Add environment variables:
   - `NEXT_PUBLIC_API_URL` = `https://mhoo-signal-bot.fly.dev`
   - `NEXT_PUBLIC_WS_URL` = `wss://mhoo-signal-bot.fly.dev`
4. Deploy

Every `git push main` auto-deploys the frontend.

### Option B: CLI

```bash
cd frontend
vercel --prod
```

### After first deploy

Update backend CORS to allow your Vercel URL:

```bash
fly secrets set CORS_ORIGINS="https://mhoo-signal-bot.vercel.app"
```

---

## Environment Variables Reference

### Backend (Fly secrets)

| Variable | Required | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✅ | Discord Developer Portal |
| `DISCORD_CHANNEL_ID` | ✅ | Right-click channel → Copy ID |
| `DISCORD_GUILD_ID` | ✅ | Right-click server → Copy ID |
| `SECRET_KEY` | ✅ | Random hex string |
| `CORS_ORIGINS` | ✅ | Your Vercel URL |
| `BINANCE_API_KEY` | Optional | Only needed for order execution |
| `BINANCE_API_SECRET` | Optional | Only needed for order execution |
| `CRYPTOPANIC_API_KEY` | Optional | News sentiment scoring |

Set in `fly.toml` (no need to set as secrets):

| Variable | Value |
|---|---|
| `DATABASE_URL` | `sqlite:////data/signalbot.db` |
| `ML_MODELS_DIR` | `/data/ml_models` |
| `ML_HISTORY_DIR` | `/data/ml_history` |

### Frontend (Vercel env vars)

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://mhoo-signal-bot.fly.dev` |
| `NEXT_PUBLIC_WS_URL` | `wss://mhoo-signal-bot.fly.dev` |

---

## Free Tier Limits

### Fly.io (always-on, never sleeps)
- 3 shared-cpu-1x VMs, 256MB RAM each (we use 1)
- 3 GB persistent storage (we use 1 GB)
- 160 GB outbound transfer/month

### Vercel (CDN, always-on)
- 100 GB bandwidth/month
- Unlimited deployments

---

## Local Development

```bash
# Backend
cd backend
cp ../.env.example .env
./venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (separate terminal)
cd frontend
npm run dev    # http://localhost:3000
```
