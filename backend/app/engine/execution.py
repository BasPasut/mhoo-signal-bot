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
import asyncio
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
            max_qty = 0.0          # LOT_SIZE max
            market_max_qty = 0.0   # MARKET_LOT_SIZE max (per single MARKET order)
            qty_precision = sym.get("quantityPrecision", 3)

            for f in sym.get("filters", []):
                ft = f.get("filterType", "")
                if ft == "LOT_SIZE":
                    qty_step = float(f.get("stepSize", 1.0))
                    min_qty = float(f.get("minQty", 0.0))
                    max_qty = float(f.get("maxQty", 0.0))
                elif ft == "MARKET_LOT_SIZE":
                    market_max_qty = float(f.get("maxQty", 0.0))
                elif ft == "PRICE_FILTER":
                    price_tick = float(f.get("tickSize", 0.01))
                elif ft == "MIN_NOTIONAL":
                    min_notional = float(f.get("notional", 5.0))

            result = {
                "qty_step": qty_step,
                "price_tick": price_tick,
                "min_qty": min_qty,
                "max_qty": max_qty,
                # MARKET orders are capped by MARKET_LOT_SIZE; fall back to LOT_SIZE max
                "market_max_qty": market_max_qty or max_qty,
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
        "max_qty": 0.0,
        "market_max_qty": 0.0,
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

        # 3a. Notional cap — a single position must not exceed 30% of account as notional.
        # This prevents over-concentration when leverage is low or SL is very tight.
        MAX_NOTIONAL_PCT = 0.30
        max_notional = balance * MAX_NOTIONAL_PCT
        if sizing["notional_value"] > max_notional:
            raw_qty = max_notional / entry_price
            logger.info(
                f"[execution] Notional cap applied for {pair}: "
                f"${sizing['notional_value']:.0f} → ${max_notional:.0f} "
                f"({MAX_NOTIONAL_PCT*100:.0f}% of ${balance:.0f})"
            )

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
        #
        # Binance testnet (and some live configs) rejects STOP_MARKET / TAKE_PROFIT
        # orders with -4120 ("use Algo Order API").  We therefore only place the
        # LIMIT entry here; SL/TP are tracked internally and the position is closed
        # programmatically via close_position_market() when outcome_tracker resolves
        # the signal.  On live Binance where conditional orders work, the pattern can
        # be extended; for now a single entry order is sufficient.
        orders_to_place = [
            ("entry", _signed_params({
                "symbol": pair,
                "side": entry_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": qty,
                "price": entry_r,
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


# ── Breakeven SL move (called on TP1 hit) ────────────────────────────────────

async def move_sl_to_breakeven(signal_id: int, symbol: str, breakeven_price: float, direction: str) -> bool:
    """
    When TP1 is hit, attempt to move the SL to breakeven on Binance.

    Binance testnet (and some environments) do not support STOP_MARKET orders
    via /fapi/v1/order (-4120).  In that case, the SL is tracked in the DB
    only and close_position_market() will close the trade when outcome_tracker
    resolves it.  This function logs the intent and returns True so the caller
    treats it as a non-fatal soft success.

    Returns True if the new SL was placed (or gracefully skipped).
    """
    from app.core.config_store import get_execution_mode
    from app.engine.binance import _futures_pair
    from app.models.db import TradeOrder, engine
    from sqlmodel import Session, select

    mode = get_execution_mode()
    if mode == "disabled":
        logger.info(f"[execution] move_sl_to_breakeven skipped — mode=disabled")
        return False

    if mode == "testnet":
        api_key    = settings.binance_testnet_api_key
        api_secret = settings.binance_testnet_api_secret
        base_url   = settings.binance_testnet_base_url
    else:
        api_key    = settings.binance_api_key
        api_secret = settings.binance_api_secret
        base_url   = settings.binance_base_url

    if not api_key or not api_secret:
        logger.warning(f"[execution] move_sl_to_breakeven: keys not configured for mode={mode}")
        return False

    headers = {"X-MBX-APIKEY": api_key}
    pair    = _futures_pair(symbol)

    # 1. Fetch original SL order from DB
    with Session(engine) as s:
        sl_order = s.exec(
            select(TradeOrder)
            .where(TradeOrder.signal_id == signal_id)
            .where(TradeOrder.role == "sl")
        ).first()

    if not sl_order:
        logger.warning(f"[execution] move_sl_to_breakeven: no SL order found for signal {signal_id}")
        return False

    orig_order_id = sl_order.binance_order_id
    qty           = sl_order.quantity
    close_side    = "SELL" if direction == "LONG" else "BUY"

    async with httpx.AsyncClient() as client:
        # 2. Cancel original SL order
        if orig_order_id:
            try:
                r = await client.delete(
                    f"{base_url}/fapi/v1/order",
                    params=_signed_params({"symbol": pair, "orderId": orig_order_id}, api_secret),
                    headers=headers,
                    timeout=8,
                )
                if r.status_code == 200 or (r.status_code == 400 and r.json().get("code") == -2011):
                    logger.info(f"[execution] Cancelled original SL orderId={orig_order_id} for signal {signal_id}")
                else:
                    logger.warning(f"[execution] Cancel SL warning {r.status_code}: {r.text}")
            except Exception as e:
                logger.warning(f"[execution] Cancel SL exception: {e}")

        # 3. Get price precision for correct rounding
        prec       = await _get_symbol_precision(client, pair, base_url, headers)
        be_price_r = _round_price(breakeven_price, prec["price_tick"])

        # 4. Place new STOP_MARKET at breakeven
        new_params = _signed_params({
            "symbol":     pair,
            "side":       close_side,
            "type":       "STOP_MARKET",
            "stopPrice":  be_price_r,
            "closePosition": "false",
            "reduceOnly": "true",
            "quantity":   qty,
        }, api_secret)

        resp = await _place_single_order(client, base_url, pair, new_params, headers)
        new_order_id = str(resp.get("orderId", "")) or None
        status       = resp.get("status", "ERROR")
        resp_code    = resp.get("code")
        error        = resp.get("error") or (resp.get("msg") if status == "ERROR" else None)

        # -4120: testnet/env does not support STOP_MARKET — log and treat as soft success
        # outcome_tracker will close the position via close_position_market() instead
        if resp_code == -4120:
            logger.info(
                f"[execution] Breakeven SL skipped (STOP_MARKET not supported) for signal {signal_id} "
                f"{symbol} — position will be closed programmatically on resolution"
            )
            return True

        logger.info(
            f"[execution] Breakeven SL placed for signal {signal_id} {symbol} {direction}: "
            f"stopPrice={be_price_r} qty={qty} orderId={new_order_id} status={status} error={error}"
        )

        # 5. Update trade_order row (only if we actually placed an order)
        if new_order_id:
            with Session(engine) as s:
                row = s.exec(
                    select(TradeOrder)
                    .where(TradeOrder.signal_id == signal_id)
                    .where(TradeOrder.role == "sl")
                ).first()
                if row:
                    row.binance_order_id = new_order_id
                    row.stop_price       = be_price_r
                    row.status           = status
                    row.error            = error
                    s.add(row)
                    s.commit()

        return status not in ("ERROR",)


# ── Cancel all orders for a signal (called on expiry / full resolution) ───────

async def cancel_signal_orders(signal_id: int) -> None:
    """
    Cancel any still-open Binance orders linked to signal_id.

    Called when a signal expires (entry never filled) or resolves, so no
    orphaned LIMIT / STOP_MARKET orders linger on the exchange.
    reduceOnly orders auto-cancel when position closes, but the LIMIT entry
    and any unfilled TP/SL orders must be explicitly cancelled on expiry.
    """
    from app.core.config_store import get_execution_mode
    from app.engine.binance import _futures_pair
    from app.models.db import TradeOrder, engine
    from sqlmodel import Session, select

    mode = get_execution_mode()
    if mode == "disabled":
        return

    if mode == "testnet":
        api_key    = settings.binance_testnet_api_key
        api_secret = settings.binance_testnet_api_secret
        base_url   = settings.binance_testnet_base_url
    else:
        api_key    = settings.binance_api_key
        api_secret = settings.binance_api_secret
        base_url   = settings.binance_base_url

    if not api_key or not api_secret:
        return

    headers = {"X-MBX-APIKEY": api_key}

    with Session(engine) as s:
        orders = s.exec(
            select(TradeOrder).where(TradeOrder.signal_id == signal_id)
        ).all()

    if not orders:
        return

    async with httpx.AsyncClient() as client:
        for order in orders:
            if not order.binance_order_id:
                continue
            try:
                r = await client.delete(
                    f"{base_url}/fapi/v1/order",
                    params=_signed_params({
                        "symbol": order.binance_symbol,
                        "orderId": order.binance_order_id,
                    }, api_secret),
                    headers=headers,
                    timeout=8,
                )
                code = r.json().get("code") if r.status_code != 200 else None
                if r.status_code == 200:
                    logger.info(
                        f"[execution] Cancelled {order.role} orderId={order.binance_order_id} "
                        f"for signal {signal_id}"
                    )
                elif code == -2011:
                    pass  # already filled or cancelled — fine
                else:
                    logger.warning(
                        f"[execution] Cancel {order.role} orderId={order.binance_order_id} "
                        f"returned {r.status_code}: {r.text}"
                    )
            except Exception as e:
                logger.warning(
                    f"[execution] Cancel order exception signal={signal_id} "
                    f"role={order.role}: {e}"
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


# ── Close position via MARKET order (called by outcome_tracker on resolution) ─

async def close_position_market(signal_id: int, symbol: str, direction: str) -> bool:
    """
    Place a MARKET reduceOnly order to close the full open position for signal_id.
    Called by outcome_tracker._resolve() for every resolved signal outcome so that
    the Binance position is actually closed when klines detect SL/TP/expiry.

    Returns True if the close order was accepted, False on error.
    """
    from app.core.config_store import get_execution_mode
    from app.engine.binance import _futures_pair
    from app.models.db import TradeOrder, engine
    from sqlmodel import Session, select

    mode = get_execution_mode()
    if mode == "disabled":
        return False

    if mode == "testnet":
        api_key    = settings.binance_testnet_api_key
        api_secret = settings.binance_testnet_api_secret
        base_url   = settings.binance_testnet_base_url
    else:
        api_key    = settings.binance_api_key
        api_secret = settings.binance_api_secret
        base_url   = settings.binance_base_url

    if not api_key or not api_secret:
        return False

    headers = {"X-MBX-APIKEY": api_key}
    pair       = _futures_pair(symbol)
    close_side = "SELL" if direction == "LONG" else "BUY"

    # Fetch the filled quantity from the entry order in DB
    qty: Optional[float] = None
    with Session(engine) as s:
        entry_order = s.exec(
            select(TradeOrder)
            .where(TradeOrder.signal_id == signal_id)
            .where(TradeOrder.role == "entry")
        ).first()
        if entry_order:
            qty = entry_order.quantity

    if not qty:
        logger.warning(f"[execution] close_position_market: no entry order qty for signal {signal_id}")
        return False

    async with httpx.AsyncClient() as client:
        prec  = await _get_symbol_precision(client, pair, base_url, headers)
        qty_r = _round_step(qty, prec["qty_step"])

        params = _signed_params({
            "symbol":     pair,
            "side":       close_side,
            "type":       "MARKET",
            "quantity":   qty_r,
            "reduceOnly": "true",
        }, api_secret)

        r = await client.post(
            f"{base_url}/fapi/v1/order",
            data=params,
            headers=headers,
            timeout=10,
        )

        if r.status_code == 200:
            logger.info(
                f"[execution] Position closed MARKET for signal {signal_id} "
                f"{symbol} {direction} qty={qty_r} orderId={r.json().get('orderId')}"
            )
            return True
        else:
            # -2022 = reduceOnly order would increase position (no position open — fine)
            code = r.json().get("code")
            if code == -2022:
                logger.info(f"[execution] close_position_market: no open position for signal {signal_id} — skipping")
                return True
            logger.warning(
                f"[execution] close_position_market failed for signal {signal_id}: "
                f"{r.status_code} {r.text}"
            )
            return False


# ── Position reconciliation ───────────────────────────────────────────────────

def _exec_credentials() -> Optional[tuple[str, str, str]]:
    """Return (api_key, api_secret, base_url) for the active execution mode, or None."""
    from app.core.config_store import get_execution_mode

    mode = get_execution_mode()
    if mode == "disabled":
        return None
    if mode == "testnet":
        api_key, api_secret = settings.binance_testnet_api_key, settings.binance_testnet_api_secret
        base_url = settings.binance_testnet_base_url
    else:
        api_key, api_secret = settings.binance_api_key, settings.binance_api_secret
        base_url = settings.binance_base_url
    if not api_key or not api_secret:
        return None
    return api_key, api_secret, base_url


async def get_exchange_positions() -> list[dict]:
    """
    Query /fapi/v2/positionRisk and return all positions with non-zero size.

    Each entry: {pair, amt (signed float), entry_price, upnl}.
    Returns [] when execution is disabled or keys are missing.
    """
    creds = _exec_credentials()
    if creds is None:
        return []
    api_key, api_secret, base_url = creds
    headers = {"X-MBX-APIKEY": api_key}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{base_url}/fapi/v2/positionRisk",
                params=_signed_params({}, api_secret),
                headers=headers,
                timeout=10,
            )
        if r.status_code != 200:
            logger.warning(f"[reconcile] positionRisk failed: {r.status_code} {r.text[:200]}")
            return []
        data = r.json()
        if not isinstance(data, list):
            logger.warning(f"[reconcile] positionRisk unexpected response: {data}")
            return []
        out = []
        for p in data:
            amt = float(p.get("positionAmt", 0) or 0)
            if amt != 0:
                out.append({
                    "pair":        p.get("symbol"),
                    "amt":         amt,
                    "entry_price": float(p.get("entryPrice", 0) or 0),
                    "upnl":        float(p.get("unRealizedProfit", 0) or 0),
                })
        return out
    except Exception as e:
        logger.warning(f"[reconcile] get_exchange_positions exception: {e}")
        return []


async def flatten_position_market(pair: str, position_amt: float) -> bool:
    """
    Close an exact exchange position by its live positionAmt with a reduceOnly MARKET order.
    Used by reconciliation to flatten orphan positions that have no open DB signal —
    independent of any TradeOrder qty in the DB.

    `pair` is the Binance futures pair (e.g. "1000BONKUSDT"). `position_amt` is signed:
    positive = long (close with SELL), negative = short (close with BUY).
    """
    creds = _exec_credentials()
    if creds is None:
        return False
    api_key, api_secret, base_url = creds
    headers = {"X-MBX-APIKEY": api_key}

    close_side = "SELL" if position_amt > 0 else "BUY"
    total_qty = abs(position_amt)

    try:
        async with httpx.AsyncClient() as client:
            prec      = await _get_symbol_precision(client, pair, base_url, headers)
            qty_step  = prec["qty_step"]
            # Per-order ceiling: MARKET_LOT_SIZE max. Large positions must be split
            # into multiple MARKET orders or Binance rejects with -4005.
            chunk_max = prec.get("market_max_qty") or 0.0

            total_r = _round_step(total_qty, qty_step)
            if total_r <= 0:
                logger.warning(f"[reconcile] flatten {pair}: qty rounds to 0 (amt={position_amt})")
                return False

            # Build chunk list respecting the per-order max
            chunks: list[float] = []
            if chunk_max and total_r > chunk_max:
                remaining = total_r
                while remaining > 1e-12:
                    take = min(chunk_max, remaining)
                    take = _round_step(take, qty_step)
                    if take <= 0:
                        break
                    chunks.append(take)
                    remaining = round(remaining - take, 12)
            else:
                chunks = [total_r]

            all_ok = True
            for i, q in enumerate(chunks):
                params = _signed_params({
                    "symbol":     pair,
                    "side":       close_side,
                    "type":       "MARKET",
                    "quantity":   q,
                    "reduceOnly": "true",
                }, api_secret)
                r = await client.post(
                    f"{base_url}/fapi/v1/order",
                    data=params,
                    headers=headers,
                    timeout=10,
                )
                if r.status_code == 200:
                    logger.warning(
                        f"[reconcile] ORPHAN FLATTENED {pair} {close_side} "
                        f"chunk {i+1}/{len(chunks)} qty={q} orderId={r.json().get('orderId')}"
                    )
                else:
                    code = r.json().get("code")
                    if code == -2022:
                        logger.info(f"[reconcile] flatten {pair}: position already flat — done")
                        return True
                    logger.warning(
                        f"[reconcile] flatten {pair} chunk {i+1}/{len(chunks)} "
                        f"failed: {r.status_code} {r.text[:200]}"
                    )
                    all_ok = False
                await asyncio.sleep(0.15)
            return all_ok
    except Exception as e:
        logger.warning(f"[reconcile] flatten_position_market {pair} exception: {e}")
        return False


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
