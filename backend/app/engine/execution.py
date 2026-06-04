"""
Execution layer — Binance Futures order placement.

Supports two execution modes (selected at runtime via config_store):
  testnet  →  testnet.binancefuture.com  +  BINANCE_TESTNET_API_KEY/SECRET
  live     →  fapi.binance.com           +  BINANCE_API_KEY/SECRET

On a signal fire, place_signal_orders() places 4 orders:
  entry   LIMIT order at signal entry_price
  sl      STOP_MARKET at signal sl (reduceOnly)
  tp1     TAKE_PROFIT LIMIT at signal tp1 — 50% of qty (reduceOnly)
  tp2     TAKE_PROFIT LIMIT at signal tp2 — remaining 50% (reduceOnly)

All orders are saved to the trade_order table linked to the signal_id.
"""
import functools
import hmac
import hashlib
import math
import time
import logging
from datetime import datetime
from typing import Optional

import httpx

from app.core.settings import settings

logger = logging.getLogger(__name__)

# ── Precision cache ───────────────────────────────────────────────────────────
_precision_cache: dict = {}


# ── Rounding helpers ──────────────────────────────────────────────────────────

def _round_step(value: float, step: float) -> float:
    """Floor-round value to Binance stepSize precision."""
    precision = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    result = math.floor(value / step) * step
    return round(result, precision)


def _round_price(value: float, tick: float) -> float:
    """Round price to Binance tickSize precision."""
    precision = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    result = round(round(value / tick) * tick, precision)
    return result


# ── HMAC signing ──────────────────────────────────────────────────────────────

def _signed_params(params: dict, secret: str) -> dict:
    """Add timestamp + HMAC sha256 signature to params dict."""
    ts = int(time.time() * 1000)
    params["timestamp"] = ts
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params


# ── Balance fetch ─────────────────────────────────────────────────────────────

async def _get_balance(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    api_secret: str,
) -> float:
    """Fetch available USDT from /fapi/v2/balance."""
    try:
        params = _signed_params({}, api_secret)
        r = await client.get(
            f"{base_url}/fapi/v2/balance",
            params=params,
            headers={"X-MBX-APIKEY": api_key},
            timeout=8,
        )
        r.raise_for_status()
        for asset in r.json():
            if asset.get("asset") == "USDT":
                return float(asset.get("availableBalance", 0))
    except Exception as e:
        logger.warning(f"[execution] _get_balance failed: {e}")
    return 0.0


# ── Symbol precision ──────────────────────────────────────────────────────────

async def _get_symbol_precision(
    client: httpx.AsyncClient,
    pair: str,
    base_url: str,
    headers: dict,
) -> dict:
    """
    Fetch symbol precision from /fapi/v1/exchangeInfo.
    Returns dict with: qty_step, price_tick, min_qty, min_notional, qty_precision
    """
    if pair in _precision_cache:
        return _precision_cache[pair]

    try:
        r = await client.get(
            f"{base_url}/fapi/v1/exchangeInfo",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        for sym in data.get("symbols", []):
            if sym["symbol"] != pair:
                continue
            qty_step = 1.0
            price_tick = 0.01
            min_qty = 0.0
            min_notional = 5.0
            qty_precision = sym.get("quantityPrecision", 3)

            for f in sym.get("filters", []):
                ft = f.get("filterType", "")
                if ft == "LOT_SIZE":
                    qty_step = float(f.get("stepSize", 1.0))
                    min_qty = float(f.get("minQty", 0.0))
                elif ft == "PRICE_FILTER":
                    price_tick = float(f.get("tickSize", 0.01))
                elif ft == "MIN_NOTIONAL":
                    min_notional = float(f.get("notional", 5.0))

            result = {
                "qty_step": qty_step,
                "price_tick": price_tick,
                "min_qty": min_qty,
                "min_notional": min_notional,
                "qty_precision": qty_precision,
            }
            _precision_cache[pair] = result
            return result
    except Exception as e:
        logger.warning(f"[execution] _get_symbol_precision({pair}) failed: {e}")

    # fallback defaults
    return {
        "qty_step": 0.001,
        "price_tick": 0.01,
        "min_qty": 0.001,
        "min_notional": 5.0,
        "qty_precision": 3,
    }


# ── Leverage / margin type ────────────────────────────────────────────────────

async def _set_leverage(
    client: httpx.AsyncClient,
    base_url: str,
    pair: str,
    leverage: int,
    api_key: str,
    api_secret: str,
    headers: dict,
):
    """POST /fapi/v1/leverage — swallow errors (symbol may already be set)."""
    try:
        params = _signed_params({"symbol": pair, "leverage": leverage}, api_secret)
        await client.post(
            f"{base_url}/fapi/v1/leverage",
            data=params,
            headers=headers,
            timeout=8,
        )
    except Exception as e:
        logger.warning(f"[execution] _set_leverage({pair}, {leverage}) warning: {e}")


async def _set_margin_type(
    client: httpx.AsyncClient,
    base_url: str,
    pair: str,
    margin_type: str,
    api_key: str,
    api_secret: str,
    headers: dict,
):
    """POST /fapi/v1/marginType — swallow -4046 already-set error."""
    try:
        params = _signed_params({"symbol": pair, "marginType": margin_type}, api_secret)
        r = await client.post(
            f"{base_url}/fapi/v1/marginType",
            data=params,
            headers=headers,
            timeout=8,
        )
        if r.status_code not in (200, 400):
            r.raise_for_status()
        # -4046 means "No need to change margin type" — that's fine
    except Exception as e:
        logger.warning(f"[execution] _set_margin_type({pair}) warning: {e}")


# ── Single order placement ────────────────────────────────────────────────────

async def _place_single_order(
    client: httpx.AsyncClient,
    base_url: str,
    pair: str,
    params: dict,
    headers: dict,
) -> dict:
    """POST /fapi/v1/order — return response JSON or error dict."""
    try:
        r = await client.post(
            f"{base_url}/fapi/v1/order",
            data=params,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


# ── Main entry point ──────────────────────────────────────────────────────────

async def place_signal_orders(signal: dict, signal_id: int) -> None:
    """
    Place 4 Binance Futures orders for a fired signal:
      entry (LIMIT), sl (STOP_MARKET), tp1 (TAKE_PROFIT), tp2 (TAKE_PROFIT)

    Saves each order result to the trade_order table.
    """
    from app.core.config_store import get_execution_mode
    from app.engine.binance import _futures_pair
    from app.models.db import TradeOrder, engine
    from sqlmodel import Session

    mode = get_execution_mode()
    if mode == "disabled":
        logger.debug("[execution] Mode is disabled — skipping order placement")
        return

    # Select keys + URL based on mode
    if mode == "testnet":
        api_key = settings.binance_testnet_api_key
        api_secret = settings.binance_testnet_api_secret
        base_url = settings.binance_testnet_base_url
    else:  # live
        api_key = settings.binance_api_key
        api_secret = settings.binance_api_secret
        base_url = settings.binance_base_url

    if not api_key or not api_secret:
        logger.warning(f"[execution] Mode={mode} but API keys not configured — skipping")
        return

    headers = {"X-MBX-APIKEY": api_key}
    symbol = signal.get("symbol", "")
    pair = _futures_pair(symbol)
    direction = signal.get("direction", "LONG")
    entry_price = float(signal.get("entry_price", 0))
    sl_price = float(signal.get("sl", 0))
    tp1_price = float(signal.get("tp1", 0))
    tp2_price = float(signal.get("tp2", 0))
    risk_pct = float(signal.get("position_risk_pct") or 1.25)
    leverage = int(signal.get("leverage") or 10)

    if entry_price <= 0:
        logger.warning(f"[execution] Invalid entry_price={entry_price} for {symbol} — skipping")
        return

    async with httpx.AsyncClient() as client:
        # 1. Fetch balance
        balance = await _get_balance(client, base_url, api_key, api_secret)
        if balance <= 0:
            logger.warning(f"[execution] Balance=0 for mode={mode} — skipping {symbol}")
            return

        # 2. Get precision
        prec = await _get_symbol_precision(client, pair, base_url, headers)
        qty_step = prec["qty_step"]
        price_tick = prec["price_tick"]
        min_qty = prec["min_qty"]
        min_notional = prec["min_notional"]

        # 3. Calc position size
        sizing = calc_position_size(
            account_equity=balance,
            risk_pct=risk_pct,
            entry_price=entry_price,
            sl_price=sl_price,
            leverage=leverage,
        )
        raw_qty = sizing["contracts"]
        qty = _round_step(raw_qty, qty_step)

        # 4. Validate size
        if qty < min_qty:
            logger.warning(
                f"[execution] qty={qty} < min_qty={min_qty} for {pair} — skipping "
                f"(balance={balance:.2f}, risk_pct={risk_pct}%)"
            )
            return
        if qty * entry_price < min_notional:
            logger.warning(
                f"[execution] notional={qty * entry_price:.2f} < min_notional={min_notional} "
                f"for {pair} — skipping"
            )
            return

        # 5. Set leverage + margin type
        await _set_leverage(client, base_url, pair, leverage, api_key, api_secret, headers)
        await _set_margin_type(client, base_url, pair, "ISOLATED", api_key, api_secret, headers)

        # 6. Determine sides
        entry_side = "BUY" if direction == "LONG" else "SELL"
        close_side = "SELL" if direction == "LONG" else "BUY"

        # 7. Split qty for tp1 / tp2
        qty1 = _round_step(qty * 0.5, qty_step)
        qty2 = _round_step(qty - qty1, qty_step)
        if qty2 <= 0:
            qty2 = qty1

        # 8. Round prices
        entry_r = _round_price(entry_price, price_tick)
        sl_r = _round_price(sl_price, price_tick)
        tp1_r = _round_price(tp1_price, price_tick)
        tp2_r = _round_price(tp2_price, price_tick)

        # 9. Build order params list: (role, params_dict)
        orders_to_place = [
            ("entry", _signed_params({
                "symbol": pair,
                "side": entry_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": qty,
                "price": entry_r,
            }, api_secret)),
            ("sl", _signed_params({
                "symbol": pair,
                "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_r,
                "closePosition": "false",
                "reduceOnly": "true",
                "quantity": qty,
            }, api_secret)),
            ("tp1", _signed_params({
                "symbol": pair,
                "side": close_side,
                "type": "TAKE_PROFIT",
                "timeInForce": "GTC",
                "quantity": qty1,
                "price": tp1_r,
                "stopPrice": tp1_r,
                "reduceOnly": "true",
            }, api_secret)),
            ("tp2", _signed_params({
                "symbol": pair,
                "side": close_side,
                "type": "TAKE_PROFIT",
                "timeInForce": "GTC",
                "quantity": qty2,
                "price": tp2_r,
                "stopPrice": tp2_r,
                "reduceOnly": "true",
            }, api_secret)),
        ]

        # 10. Place orders + save to DB
        results = []
        for role, params in orders_to_place:
            resp = await _place_single_order(client, base_url, pair, params, headers)
            status = resp.get("status", "ERROR")
            error = resp.get("error") or (resp.get("msg") if status == "ERROR" else None)
            binance_order_id = str(resp.get("orderId", "")) or None

            order_row = TradeOrder(
                signal_id=signal_id,
                binance_order_id=binance_order_id,
                symbol=symbol,
                binance_symbol=pair,
                side=params.get("side", ""),
                order_type=params.get("type", ""),
                role=role,
                quantity=float(params.get("quantity", qty)),
                price=float(params.get("price", 0.0)),
                stop_price=float(params.get("stopPrice", 0.0)),
                status=status,
                execution_mode=mode,
                error=error,
                created_at=datetime.utcnow(),
            )
            with Session(engine) as s:
                s.add(order_row)
                s.commit()

            results.append((role, status, error))
            logger.info(
                f"[execution] {mode.upper()} {pair} {role}: "
                f"status={status} orderId={binance_order_id} error={error}"
            )

        ok = sum(1 for _, s, _ in results if s not in ("ERROR",))
        logger.info(
            f"[execution] Signal {signal_id} {symbol} {direction}: "
            f"{ok}/{len(results)} orders placed (mode={mode})"
        )


# ── Live equity fetch ─────────────────────────────────────────────────────────

async def get_futures_balance(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> float:
    """
    Fetch available USDT balance from Binance Futures wallet.
    Defaults to settings values (live). Returns 0.0 on failure.
    """
    _base_url = base_url or settings.binance_base_url
    _api_key = api_key or settings.binance_api_key
    _api_secret = api_secret or settings.binance_api_secret

    if not _api_key or not _api_secret:
        return 0.0
    try:
        async with httpx.AsyncClient() as client:
            return await _get_balance(client, _base_url, _api_key, _api_secret)
    except Exception as e:
        logger.warning(f"get_futures_balance failed: {e}")
    return 0.0


# ── Equity-check decorator ────────────────────────────────────────────────────

def equity_check(default_equity: float = 1000.0):
    """
    Decorator that injects the live Binance futures USDT balance into
    `account_equity` kwarg before calling the wrapped coroutine.

    If the API is not configured or the request fails, falls back to
    `default_equity` so the system degrades gracefully.

    Example:
        @equity_check(default_equity=500.0)
        async def place_order(signal, account_equity=None):
            sizing = calc_position_size(account_equity, ...)
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if kwargs.get("account_equity") is None:
                equity = await get_futures_balance()
                if equity <= 0:
                    logger.warning(
                        f"[equity_check] API returned 0 or unavailable "
                        f"— using default ${default_equity:.2f}"
                    )
                    equity = default_equity
                else:
                    logger.info(f"[equity_check] Live equity: ${equity:.2f} USDT")
                kwargs["account_equity"] = equity
            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ── Position sizing ───────────────────────────────────────────────────────────

def calc_position_size(
    account_equity: float,
    risk_pct: float,
    entry_price: float,
    sl_price: float,
    leverage: int = 1,
) -> dict:
    """
    Calculate exact Binance Futures position size based on fixed % risk.

    Formula:
        usdt_risk       = account_equity × (risk_pct / 100)
        sl_distance_pct = |entry - sl| / entry
        notional_value  = usdt_risk / sl_distance_pct
        contracts       = notional_value / entry_price

    Args:
        account_equity: Current available USDT balance.
        risk_pct:       % of equity to risk (e.g. 1.25 for 1.25%).
        entry_price:    Signal entry price.
        sl_price:       Stop-loss price.
        leverage:       Applied leverage (for margin calculation only).

    Returns dict with all relevant sizing fields.
    """
    if entry_price <= 0 or abs(entry_price - sl_price) < 1e-10:
        return {
            "usdt_risk": 0.0,
            "sl_distance_pct": 0.0,
            "contracts": 0.0,
            "notional_value": 0.0,
            "margin_required": 0.0,
        }

    usdt_risk = account_equity * (risk_pct / 100)
    sl_distance_pct = abs(entry_price - sl_price) / entry_price
    notional_value = usdt_risk / sl_distance_pct if sl_distance_pct > 0 else 0.0
    contracts = notional_value / entry_price
    margin_required = notional_value / leverage if leverage > 0 else notional_value

    return {
        "usdt_risk": round(usdt_risk, 2),
        "sl_distance_pct": round(sl_distance_pct * 100, 3),
        "contracts": round(contracts, 6),
        "notional_value": round(notional_value, 2),
        "margin_required": round(margin_required, 2),
    }


# ── Example: decorated execution function ────────────────────────────────────

@equity_check(default_equity=1000.0)
async def compute_order(signal: dict, account_equity: Optional[float] = None) -> dict:
    """
    Enriches a signal dict with exact position sizing based on live equity.
    Does NOT place orders — call this to get sizing before sending to Binance.
    """
    risk_pct = signal.get("position_risk_pct", 1.25)
    leverage = signal.get("leverage", 1)
    sizing = calc_position_size(
        account_equity=account_equity,
        risk_pct=risk_pct,
        entry_price=signal["entry_price"],
        sl_price=signal["sl"],
        leverage=leverage,
    )
    logger.info(
        f"Order sizing [{signal['symbol']} {signal['direction']}]: "
        f"equity=${account_equity:.2f} risk={risk_pct}% "
        f"→ ${sizing['usdt_risk']:.2f} risk / "
        f"{sizing['contracts']:.6f} contracts / "
        f"${sizing['notional_value']:.2f} notional"
    )
    return {**signal, "sizing": sizing, "account_equity": account_equity}
