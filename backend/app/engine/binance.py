import httpx
import pandas as pd
import numpy as np
import asyncio
import logging
from app.core.settings import settings

logger = logging.getLogger(__name__)
BASE = settings.binance_base_url

# Binance Futures uses 1000x contracts for micro-cap coins
_SYMBOL_OVERRIDES = {
    "SHIB": "1000SHIB",
    "PEPE": "1000PEPE",
    "BONK": "1000BONK",
    "FLOKI": "1000FLOKI",
    "RATS": "1000RATS",
    "SATS": "1000SATS",
    "MNT": "MANTLE",
}

# Persistent connection pool — reused across all API calls instead of
# creating a new TCP+SSL handshake per request (was ~500 handshakes/scan).
_http: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=30,
                max_keepalive_connections=15,
                keepalive_expiry=30,
            ),
            timeout=10,
        )
    return _http


async def close_http_client():
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()
        _http = None


def _futures_pair(symbol: str) -> str:
    base = symbol.upper().replace("USDT", "")
    base = _SYMBOL_OVERRIDES.get(base, base)
    return f"{base}USDT"


async def _get(path: str, params: dict = None) -> dict | list:
    client = get_http_client()
    for attempt in range(3):
        try:
            r = await client.get(f"{BASE}{path}", params=params)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)


async def get_klines(
    symbol: str,
    interval: str,
    limit: int = 500,
    start_ms: int | None = None,
) -> pd.DataFrame:
    pair = _futures_pair(symbol)
    params: dict = {"symbol": pair, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    data = await _get("/fapi/v1/klines", params)
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_vol","trades","taker_base","taker_quote","ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open","high","low","close","volume","quote_vol"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df


async def get_funding_rate(symbol: str) -> float:
    pair = _futures_pair(symbol)
    try:
        data = await _get("/fapi/v1/premiumIndex", {"symbol": pair})
        return float(data.get("lastFundingRate", 0))
    except Exception:
        return 0.0


async def get_current_price(symbol: str) -> float:
    pair = _futures_pair(symbol)
    data = await _get("/fapi/v1/ticker/price", {"symbol": pair})
    return float(data["price"])


async def get_order_book_imbalance(symbol: str, limit: int = 20) -> float:
    pair = _futures_pair(symbol)
    try:
        book = await _get("/fapi/v1/depth", {"symbol": pair, "limit": limit})
        bid_vol = sum(float(b[1]) for b in book["bids"])
        ask_vol = sum(float(a[1]) for a in book["asks"])
        total = bid_vol + ask_vol
        return (bid_vol - ask_vol) / total if total > 0 else 0.0
    except Exception:
        return 0.0


async def get_fear_greed() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1")
            d = r.json()["data"][0]
            return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception:
        return {"value": 50, "label": "Neutral"}


async def get_open_interest_change(symbol: str) -> float:
    """
    Returns OI change % over last 5 bars as a [-1, 1] sentiment signal.
    Rising OI + rising price = strong trend confirmation.
    Rising OI + falling price = bearish (shorts piling in).
    """
    pair = _futures_pair(symbol)
    try:
        data = await _get("/futures/data/openInterestHist", {
            "symbol": pair, "period": "1h", "limit": 6
        })
        if not data or len(data) < 2:
            return 0.0
        oi_values = [float(d["sumOpenInterest"]) for d in data]
        oi_change_pct = (oi_values[-1] - oi_values[0]) / oi_values[0] if oi_values[0] > 0 else 0.0
        return float(np.clip(oi_change_pct / 0.05, -1.0, 1.0))
    except Exception:
        return 0.0


async def get_news_sentiment(symbol: str, api_key: str = "") -> float:
    if not api_key:
        return 0.0
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": api_key, "currencies": symbol,
                        "filter": "hot", "public": "true"},
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
