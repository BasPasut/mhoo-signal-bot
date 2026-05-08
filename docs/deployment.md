# Deployment Guide

## Overview

| Service | Platform | Cost |
|---|---|---|
| Backend (FastAPI + engine + Discord) | Railway | Free ($5 credit/mo) |
| Frontend (Next.js) | Vercel | Free |
| Database | SQLite on Railway volume | Free (included) |

No Redis. No MongoDB. No extra services.

---

## Step 1 — Discord Bot Setup

1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it "Signal Bot"
3. Go to **Bot** tab → click **Add Bot**
4. Copy the **Bot Token** → save as `DISCORD_BOT_TOKEN`
5. Enable **Message Content Intent** under Privileged Gateway Intents
6. Go to **OAuth2 → URL Generator**
   - Scopes: `bot`
   - Permissions: `Send Messages`, `Embed Links`, `View Channels`
7. Open the generated URL → invite the bot to your server
8. In Discord, right-click your signal channel → **Copy Channel ID** → save as `DISCORD_CHANNEL_ID`

---

## Step 2 — Railway (Backend)

1. Go to https://railway.app → sign up with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Select `BasPasut/binance-signal-bot`
4. Set **Root Directory** to `backend`
5. Railway auto-detects the Dockerfile
6. Go to **Variables** and add all values from `.env.example`:
   - `DISCORD_BOT_TOKEN`
   - `DISCORD_CHANNEL_ID`
   - `DISCORD_GUILD_ID`
   - `SECRET_KEY` (generate a random string)
   - `CORS_ORIGINS` (add your Vercel URL after step 3)
   - Optional: `CRYPTOPANIC_API_KEY`
7. Add a **Volume** (for SQLite persistence):
   - Mount path: `/app`
8. Click **Deploy**
9. Copy your Railway domain (e.g. `https://your-app.up.railway.app`)

---

## Step 3 — Vercel (Frontend)

1. Go to https://vercel.com → sign up with GitHub
2. Click **Add New → Project**
3. Import `BasPasut/binance-signal-bot`
4. Set **Root Directory** to `frontend`
5. Add Environment Variables:
   - `NEXT_PUBLIC_API_URL` = your Railway URL (e.g. `https://your-app.up.railway.app`)
   - `NEXT_PUBLIC_WS_URL` = same but with `wss://` (e.g. `wss://your-app.up.railway.app`)
6. Click **Deploy**

---

## Step 4 — Update CORS

Back in Railway → Variables → update `CORS_ORIGINS` to include your Vercel URL:
```
CORS_ORIGINS=https://your-app.vercel.app
```

Then redeploy.

---

## Step 5 — GitHub Actions (auto-deploy on push)

In your GitHub repo → Settings → Secrets → add:
- `RAILWAY_TOKEN` — from Railway → Account Settings → Tokens
- `VERCEL_TOKEN` — from Vercel → Account Settings → Tokens
- `VERCEL_ORG_ID` — from Vercel project settings
- `VERCEL_PROJECT_ID` — from Vercel project settings

Now every push to `main` auto-deploys both services.

---

## Keeping Railway Alive (optional)

Railway free tier doesn't sleep, but if you're on a limited plan:

Add a GitHub Action that pings your backend every 14 minutes:

```yaml
- cron: '*/14 * * * *'
  run: curl https://your-app.up.railway.app/api/health
```

---

## Local Development

```bash
# Terminal 1: Backend
cd backend
pip install -r requirements.txt
cp ../.env.example .env   # fill in Discord tokens
uvicorn app.main:app --reload --port 8000

# Terminal 2: Frontend
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
echo "NEXT_PUBLIC_WS_URL=ws://localhost:8000" >> .env.local
npm run dev
```

Open http://localhost:3000
