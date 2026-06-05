"""
Quick test: place a real SOLUSDT LONG position on Binance testnet.
Uses place_signal_orders() directly with a mock signal.
Run from backend/ dir: python test_place_order.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(__file__))

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import httpx, hmac, hashlib, time

BASE_URL = os.getenv("BINANCE_TESTNET_BASE_URL", "https://testnet.binancefuture.com")
API_KEY  = os.getenv("BINANCE_TESTNET_API_KEY", "")
SECRET   = os.getenv("BINANCE_TESTNET_API_SECRET", "")

def sign(params, secret):
    ts = int(time.time() * 1000)
    params["timestamp"] = ts
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

async def get_price(symbol="SOLUSDT"):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE_URL}/fapi/v1/ticker/price", params={"symbol": symbol})
        return float(r.json()["price"])

async def place_test_position():
    print(f"Base URL : {BASE_URL}")
    print(f"API Key  : {API_KEY[:8]}...")

    price = await get_price("SOLUSDT")
    print(f"SOLUSDT current price: {price}")

    # Values for a small LONG
    entry = round(price * 1.001, 2)   # slightly above market (limit)
    sl    = round(price * 0.985, 2)   # 1.5% below entry
    tp1   = round(price * 1.010, 2)   # 1% above
    tp2   = round(price * 1.020, 2)   # 2% above
    qty   = 0.20                       # 0.20 SOL ≈ $30+ (above $5 min notional)

    print(f"entry={entry}  sl={sl}  tp1={tp1}  tp2={tp2}  qty={qty}")

    headers = {"X-MBX-APIKEY": API_KEY}

    async with httpx.AsyncClient(timeout=15) as c:
        # 1) Set leverage to 5x
        r = await c.post(f"{BASE_URL}/fapi/v1/leverage",
                         params=sign({"symbol": "SOLUSDT", "leverage": 5}, SECRET),
                         headers=headers)
        print(f"Leverage: {r.status_code} {r.text[:120]}")

        # 2) Entry LIMIT order
        r = await c.post(f"{BASE_URL}/fapi/v1/order", params=sign({
            "symbol": "SOLUSDT", "side": "BUY", "type": "LIMIT",
            "timeInForce": "GTC", "quantity": qty, "price": entry,
            "positionSide": "BOTH",
        }, SECRET), headers=headers)
        print(f"Entry order: {r.status_code} {r.text[:200]}")
        if r.status_code != 200:
            print("ENTRY FAILED — aborting")
            return

        entry_data = r.json()
        entry_order_id = entry_data.get("orderId")
        print(f"  -> orderId={entry_order_id}")

        # 3) SL STOP_MARKET — must use data= (form body), not params=
        r = await c.post(f"{BASE_URL}/fapi/v1/order", data=sign({
            "symbol": "SOLUSDT", "side": "SELL", "type": "STOP_MARKET",
            "quantity": qty, "stopPrice": sl,
            "reduceOnly": "true",
        }, SECRET), headers=headers)
        print(f"SL order:    {r.status_code} {r.text[:200]}")

        # 4) TP1 — TAKE_PROFIT_MARKET (half qty)
        r = await c.post(f"{BASE_URL}/fapi/v1/order", data=sign({
            "symbol": "SOLUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET",
            "quantity": round(qty / 2, 2), "stopPrice": tp1,
            "reduceOnly": "true",
        }, SECRET), headers=headers)
        print(f"TP1 order:   {r.status_code} {r.text[:200]}")

        # 5) TP2 — TAKE_PROFIT_MARKET (half qty)
        r = await c.post(f"{BASE_URL}/fapi/v1/order", data=sign({
            "symbol": "SOLUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET",
            "quantity": round(qty / 2, 2), "stopPrice": tp2,
            "reduceOnly": "true",
        }, SECRET), headers=headers)
        print(f"TP2 order:   {r.status_code} {r.text[:200]}")

        # 6) Check open orders
        r = await c.get(f"{BASE_URL}/fapi/v1/openOrders",
                        params=sign({"symbol": "SOLUSDT"}, SECRET),
                        headers=headers)
        orders = r.json()
        print(f"\nOpen orders ({len(orders)} total):")
        for o in orders:
            print(f"  {o['orderId']} {o['type']} {o['side']} qty={o['origQty']} price={o.get('price')} stop={o.get('stopPrice')}")

        print("\nDone — check Binance testnet dashboard for positions/orders.")

asyncio.run(place_test_position())
