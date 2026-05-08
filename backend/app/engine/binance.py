import httpx
import pandas as pd
import numpy as np
import asyncio
import logging
from app.core.settings import settings

logger = logging.getLogger(__name__)
BASE = settings.binance_base_url


async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict | list:
    for attempt in range(3):
        try:
            r = await client.get(f"{BASE}{path}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)


async def get_klines(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    pair = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol
    async with httpx.AsyncClient() as client:
        data = await _get(client, "/fapi/v1/klines", {
            "symbol": pair, "interval": interval, "limit": limit
        })
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_vol","trades","taker_base","taker_quote","ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open","high","low","close","volume","quote_vol"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df


async def get_funding_rate(symbol: str) -> float:
    pair = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol
    try:
        async with httpx.AsyncClient() as client:
            data = await _get(client, "/fapi/v1/premiumIndex", {"symbol": pair})
        return float(data.get("lastFundingRate", 0))
    except Exception:
        return 0.0


async def get_current_price(symbol: str) -> float:
    pair = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol
    async with httpx.AsyncClient() as client:
        data = await _get(client, "/fapi/v1/ticker/price", {"symbol": pair})
    return float(data["price"])


async def get_order_book_imbalance(symbol: str, limit: int = 20) -> float:
    pair = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol
    try:
        async with httpx.AsyncClient() as client:
            book = await _get(client, "/fapi/v1/depth", {"symbol": pair, "limit": limit})
        bid_vol = sum(float(b[1]) for b in book["bids"])
        ask_vol = sum(float(a[1]) for a in book["asks"])
        total = bid_vol + ask_vol
        return (bid_vol - ask_vol) / total if total > 0 else 0.0
    except Exception:
        return 0.0


async def get_fear_greed() -> dict:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1", timeout=5)
            d = r.json()["data"][0]
            return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception:
        return {"value": 50, "label": "Neutral"}


async def get_news_sentiment(symbol: str, api_key: str = "") -> float:
    if not api_key:
        return 0.0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": api_key, "currencies": symbol,
                        "filter": "hot", "public": "true"},
                timeout=5,
            )
        posts = r.json().get("results", [])
        scores = []
        for p in posts[:10]:
            v = p.get("votes", {})
            pos, neg = v.get("positive", 0), v.get("negative", 0)
            if pos + neg > 0:
                scores.append((pos - neg) / (pos + neg))
        return float(np.mean(scores)) if scores else 0.0
    except Exception:
        return 0.0
