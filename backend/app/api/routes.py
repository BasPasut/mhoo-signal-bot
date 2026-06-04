from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlmodel import Session, select, desc
from typing import Optional
from datetime import datetime, timedelta
import json

from app.models.db import Signal, TradeOrder, get_session
from app.core.ws import manager
from app.core.config_store import (
    get_watchlist, set_watchlist, get_risk_profile, set_risk_profile,
    get_timeframes, set_timeframes, get_scan_interval,
    get_max_open_positions, set_max_open_positions,
    get_priority_bias, set_priority_bias,
    get_signal_tiers, set_signal_tiers,
    get_execution_mode, set_execution_mode,
    get_starting_balance, set_starting_balance,
)
from app.core.settings import settings
from app.scheduler.runner import run_now, update_interval, scheduler
from app.engine import binance
from app.discord.bot import send_test_message, send_config_change
from app.engine.dedup import get_state_summary, clear_symbol

router = APIRouter()


# ── WebSocket ─────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ── Signals ───────────────────────────────────────────────────

@router.get("/signals")
async def list_signals(
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    open_only: bool = False,
    limit: int = Query(50, le=200),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    q = select(Signal).order_by(desc(Signal.created_at)).offset(offset).limit(limit)
    if symbol:
        q = q.where(Signal.symbol == symbol.upper())
    if direction:
        q = q.where(Signal.direction == direction.upper())
    if open_only:
        q = q.where(Signal.result == None)  # noqa: E711
    signals = session.exec(q).all()
    return [_signal_to_dict(s) for s in signals]


@router.get("/signals/analytics")
async def signal_analytics(session: Session = Depends(get_session)):
    """Aggregated breakdown by grade, direction, and symbol — used by the dashboard analytics panel."""
    signals = session.exec(select(Signal)).all()

    def _tier(conf: float) -> str:
        if conf >= 80:
            return "ALPHA"
        if conf >= 60:
            return "PRIME"
        return "SETUP"

    def _bucket() -> dict:
        return {"wins": 0, "losses": 0, "open": 0}

    grade_map: dict = {"ALPHA": _bucket(), "PRIME": _bucket(), "SETUP": _bucket()}
    dir_map: dict = {"LONG": _bucket(), "SHORT": _bucket()}
    sym_map: dict = {}
    hold_times: list = []

    for s in signals:
        for mapping, key in [(grade_map, _tier(s.confidence)), (dir_map, s.direction)]:
            bkt = mapping.get(key)
            if bkt is None:
                continue
            if s.result == "win":
                bkt["wins"] += 1
            elif s.result == "loss":
                bkt["losses"] += 1
            elif s.result is None:
                bkt["open"] += 1

        if s.symbol not in sym_map:
            sym_map[s.symbol] = _bucket()
        sbkt = sym_map[s.symbol]
        if s.result == "win":
            sbkt["wins"] += 1
        elif s.result == "loss":
            sbkt["losses"] += 1
        elif s.result is None:
            sbkt["open"] += 1

        if s.result in ("win", "loss") and s.result_at and s.created_at:
            hold_times.append((s.result_at - s.created_at).total_seconds() / 3600)

    def _wr(bkt: dict):
        d = bkt["wins"] + bkt["losses"]
        return round(bkt["wins"] / d * 100, 1) if d else None

    by_symbol = sorted(
        [{"symbol": sym, **bkt, "win_rate": _wr(bkt)} for sym, bkt in sym_map.items()],
        key=lambda x: (x["wins"] + x["losses"], x["win_rate"] or 0),
        reverse=True,
    )[:10]

    return {
        "by_grade": [
            {"label": lbl, **bkt, "win_rate": _wr(bkt)} for lbl, bkt in grade_map.items()
        ],
        "by_direction": [
            {"label": lbl, **bkt, "win_rate": _wr(bkt)} for lbl, bkt in dir_map.items()
        ],
        "by_symbol": by_symbol,
        "avg_hold_hours": round(sum(hold_times) / len(hold_times), 1) if hold_times else None,
    }


@router.get("/signals/calibration")
async def signal_calibration(session: Session = Depends(get_session)):
    """Live win rate per confidence tier vs backtest expectation."""
    signals = session.exec(select(Signal)).all()
    expected = {"ALPHA": 75, "PRIME": 60, "SETUP": 45}
    buckets: dict[str, dict] = {t: {"wins": 0, "losses": 0, "total": 0} for t in expected}

    def _tier(conf: float) -> str:
        if conf >= 80:
            return "ALPHA"
        if conf >= 60:
            return "PRIME"
        return "SETUP"

    for s in signals:
        t = _tier(s.confidence)
        buckets[t]["total"] += 1
        if s.result == "win":
            buckets[t]["wins"] += 1
        elif s.result == "loss":
            buckets[t]["losses"] += 1

    result = []
    for name, data in buckets.items():
        decided = data["wins"] + data["losses"]
        result.append({
            "tier": name,
            "total": data["total"],
            "wins": data["wins"],
            "losses": data["losses"],
            "live_wr": round(data["wins"] / decided * 100, 1) if decided else None,
            "expected_wr": expected[name],
        })
    return result


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: int, session: Session = Depends(get_session)):
    s = session.get(Signal, signal_id)
    if not s:
        raise HTTPException(404, "Signal not found")
    return _signal_to_dict(s)


@router.patch("/signals/{signal_id}")
async def update_signal(signal_id: int, body: dict, session: Session = Depends(get_session)):
    s = session.get(Signal, signal_id)
    if not s:
        raise HTTPException(404, "Signal not found")
    if s.result:
        raise HTTPException(400, "Cannot update a resolved signal")
    if "leverage" in body and body["leverage"] is not None:
        lev = int(body["leverage"])
        if lev < 1 or lev > 125:
            raise HTTPException(400, "Leverage must be between 1 and 125")
        s.leverage = lev
    if "tp1" in body and body["tp1"] is not None:
        tp1 = float(body["tp1"])
        if tp1 <= 0:
            raise HTTPException(400, "TP1 must be positive")
        if s.direction == "LONG" and tp1 <= s.entry_price:
            raise HTTPException(400, "LONG TP1 must be above entry price")
        if s.direction == "SHORT" and tp1 >= s.entry_price:
            raise HTTPException(400, "SHORT TP1 must be below entry price")
        s.tp1 = tp1
        sl_dist = abs(s.entry_price - s.sl)
        tp_dist = abs(tp1 - s.entry_price)
        s.risk_reward = round(tp_dist / sl_dist, 2) if sl_dist > 0 else s.risk_reward
    if "tp2" in body and body["tp2"] is not None:
        tp2 = float(body["tp2"])
        if tp2 <= 0:
            raise HTTPException(400, "TP2 must be positive")
        if s.direction == "LONG" and tp2 <= s.entry_price:
            raise HTTPException(400, "LONG TP2 must be above entry price")
        if s.direction == "SHORT" and tp2 >= s.entry_price:
            raise HTTPException(400, "SHORT TP2 must be below entry price")
        s.tp2 = tp2
    session.add(s)
    session.commit()
    session.refresh(s)
    return {"ok": True, "leverage": s.leverage, "tp1": s.tp1, "tp2": s.tp2, "risk_reward": s.risk_reward}


@router.get("/signals/stats/summary")
async def signal_stats(session: Session = Depends(get_session)):
    signals = session.exec(select(Signal)).all()
    total = len(signals)
    wins = sum(1 for s in signals if s.result == "win")
    losses = sum(1 for s in signals if s.result == "loss")
    expired = sum(1 for s in signals if s.result == "expired")
    breakevens = sum(1 for s in signals if s.result == "breakeven")
    riding_count = sum(1 for s in signals if s.result is None and s.tp1_hit)
    open_count = sum(1 for s in signals if s.result is None and not s.tp1_hit)
    longs = sum(1 for s in signals if s.direction == "LONG")
    shorts = sum(1 for s in signals if s.direction == "SHORT")
    avg_confidence = sum(s.confidence for s in signals) / total if total else 0
    decided = wins + losses  # expired excluded from win rate
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "breakevens": breakevens,
        "open": open_count,
        "riding": riding_count,
        "win_rate": round(wins / decided * 100, 1) if decided else 0,
        "longs": longs,
        "shorts": shorts,
        "avg_confidence": round(avg_confidence, 1),
    }


# ── Config ────────────────────────────────────────────────────

@router.get("/config")
async def get_config_endpoint():
    return {
        "watchlist": get_watchlist(),
        "risk_profile": get_risk_profile(),
        "timeframes": get_timeframes(),
        "scan_interval": get_scan_interval(),
        "max_open_positions": get_max_open_positions(),
        "priority_bias": get_priority_bias(),
        "signal_tiers": get_signal_tiers(),
        "min_confidence": {
            "conservative": settings.min_confidence_conservative,
            "balanced": settings.min_confidence_balanced,
            "aggressive": settings.min_confidence_aggressive,
        },
        "execution_mode": get_execution_mode(),
        "execution_keys_configured": {
            "testnet": bool(settings.binance_testnet_api_key),
            "live": bool(settings.binance_api_key),
        },
        "starting_balance": get_starting_balance(),
    }


@router.patch("/config")
async def update_config(body: dict, background_tasks: BackgroundTasks):
    from app.core.config_store import set_config
    changes: list[dict] = []

    if "watchlist" in body:
        syms = [s.strip().upper() for s in body["watchlist"] if s.strip()]
        if not syms:
            raise HTTPException(400, "Watchlist cannot be empty")
        old = get_watchlist()
        set_watchlist(syms)
        if sorted(old) != sorted(syms):
            changes.append({"field": "watchlist", "old": old, "new": syms})

    if "risk_profile" in body:
        p = body["risk_profile"]
        if p not in ("conservative", "balanced", "aggressive"):
            raise HTTPException(400, "Invalid risk profile")
        old = get_risk_profile()
        set_risk_profile(p)
        if old != p:
            changes.append({"field": "risk_profile", "old": old, "new": p})

    if "timeframes" in body:
        valid = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
        tfs = [t.strip() for t in body["timeframes"] if t.strip() in valid]
        if not tfs:
            raise HTTPException(400, "At least one valid timeframe required")
        old = get_timeframes()
        set_timeframes(tfs)
        if sorted(old) != sorted(tfs):
            changes.append({"field": "timeframes", "old": old, "new": tfs})

    if "scan_interval" in body:
        secs = int(body["scan_interval"])
        if secs < 60:
            raise HTTPException(400, "scan_interval must be ≥ 60 seconds")
        old = get_scan_interval()
        set_config("scan_interval", str(secs))
        update_interval(secs)
        if old != secs:
            changes.append({"field": "scan_interval", "old": old, "new": secs})

    if "max_open_positions" in body:
        n = int(body["max_open_positions"])
        if not (1 <= n <= 10):
            raise HTTPException(400, "max_open_positions must be between 1 and 10")
        old = get_max_open_positions()
        set_max_open_positions(n)
        if old != n:
            changes.append({"field": "max_open_positions", "old": old, "new": n})

    if "priority_bias" in body:
        bias = body["priority_bias"]
        if bias not in ("Highest Confidence", "Lowest Risk"):
            raise HTTPException(400, "priority_bias must be 'Highest Confidence' or 'Lowest Risk'")
        old = get_priority_bias()
        set_priority_bias(bias)
        if old != bias:
            changes.append({"field": "priority_bias", "old": old, "new": bias})

    if "signal_tiers" in body:
        tiers = [t for t in body["signal_tiers"] if t in ("ALPHA", "PRIME", "SETUP")]
        if not tiers:
            raise HTTPException(400, "At least one valid signal tier required (ALPHA, PRIME, SETUP)")
        old = get_signal_tiers()
        set_signal_tiers(tiers)
        if sorted(old) != sorted(tiers):
            changes.append({"field": "signal_tiers", "old": old, "new": tiers})

    if "execution_mode" in body:
        mode = body["execution_mode"]
        if mode not in ("disabled", "testnet", "live"):
            raise HTTPException(400, "execution_mode must be 'disabled', 'testnet', or 'live'")
        if mode == "live" and not settings.binance_api_key:
            raise HTTPException(400, "Cannot enable live mode: BINANCE_API_KEY is not configured")
        if mode == "testnet" and not settings.binance_testnet_api_key:
            raise HTTPException(400, "Cannot enable testnet mode: BINANCE_TESTNET_API_KEY is not configured")
        old = get_execution_mode()
        set_execution_mode(mode)
        if old != mode:
            changes.append({"field": "execution_mode", "old": old, "new": mode})

    if "starting_balance" in body:
        bal = float(body["starting_balance"])
        if bal < 1:
            raise HTTPException(400, "starting_balance must be >= 1 USDT")
        old = get_starting_balance()
        set_starting_balance(bal)
        if old != bal:
            changes.append({"field": "starting_balance", "old": old, "new": bal})

    if changes:
        background_tasks.add_task(send_config_change, changes)

    return {"ok": True, "config": await get_config_endpoint()}


# ── Scan trigger ──────────────────────────────────────────────

@router.post("/scan/now")
async def trigger_scan():
    await run_now()
    return {"ok": True, "message": "Scan triggered"}


@router.post("/discord/test")
async def test_discord():
    try:
        await send_test_message()
        return {"ok": True, "message": "Test message sent to Discord"}
    except Exception as e:
        raise HTTPException(502, str(e))


# ── Price endpoint ────────────────────────────────────────────

@router.get("/price/{symbol}")
async def get_price(symbol: str):
    try:
        price = await binance.get_current_price(symbol.upper())
        funding = await binance.get_funding_rate(symbol.upper())
        return {"symbol": symbol.upper(), "price": price, "funding_rate": funding}
    except Exception as e:
        raise HTTPException(502, str(e))


# ── ML dataset ───────────────────────────────────────────────

@router.get("/ml/stats")
async def ml_stats(session: Session = Depends(get_session)):
    """Summary of the ML training dataset size and coverage."""
    from app.models.db import SignalFeatures
    total_features = len(session.exec(select(SignalFeatures)).all())
    labelled = session.exec(
        select(SignalFeatures).where(SignalFeatures.actual_pnl_pct != None)  # noqa: E711
    ).all()
    wins = sum(1 for r in labelled if (r.actual_pnl_pct or 0) > 0)
    return {
        "total_feature_rows": total_features,
        "labelled_rows": len(labelled),
        "unlabelled_rows": total_features - len(labelled),
        "labelled_wins": wins,
        "labelled_losses": len(labelled) - wins,
    }


@router.get("/ml/export")
async def ml_export(session: Session = Depends(get_session)):
    """
    Export complete ML training dataset as a JSON array.
    Each row = one resolved signal with full feature vector + label.
    Only returns rows where result is win or loss (labelled data).
    Load in Python: pd.DataFrame(requests.get('/api/ml/export').json())
    """
    from app.models.db import SignalFeatures

    rows = session.exec(
        select(SignalFeatures, Signal)
        .join(Signal, SignalFeatures.signal_id == Signal.id)  # type: ignore
        .where(Signal.result.in_(["win", "loss"]))  # type: ignore
    ).all()

    dataset = []
    for feat, sig in rows:
        dataset.append({
            # ── Label ──────────────────────────────────────────────
            "label": 1 if sig.result == "win" else 0,
            "result": sig.result,
            # ── Identity ───────────────────────────────────────────
            "signal_id": sig.id,
            "symbol": sig.symbol,
            "timeframe": sig.timeframe,
            "direction": sig.direction,
            "risk_profile": sig.risk_profile,
            "created_at": sig.created_at.isoformat(),
            # ── Composite scores ───────────────────────────────────
            "confidence": sig.confidence,
            "ta_score": sig.ta_score,
            "pattern_score": sig.pattern_score,
            "ml_score": sig.ml_score,
            "context_score": sig.context_score,
            # ── Price returns ──────────────────────────────────────
            "price": feat.price,
            "price_change_1h": feat.price_change_1h,
            "price_change_4h": feat.price_change_4h,
            "price_change_24h": feat.price_change_24h,
            # ── Regime / trend ─────────────────────────────────────
            "regime": feat.regime,
            "ema9_gap": feat.ema9_gap,
            "ema21_gap": feat.ema21_gap,
            "ema50_gap": feat.ema50_gap,
            "ema200_gap": feat.ema200_gap,
            # ── Momentum ───────────────────────────────────────────
            "rsi_14": feat.rsi_14,
            "rsi_slope_3": feat.rsi_slope_3,
            "macd_line": feat.macd_line,
            "macd_signal_line": feat.macd_signal_line,
            "macd_hist": feat.macd_hist,
            "macd_hist_slope": feat.macd_hist_slope,
            # ── Volatility ─────────────────────────────────────────
            "bb_pct": feat.bb_pct,
            "bb_width": feat.bb_width,
            "atr_pct": feat.atr_pct,
            # ── Trend strength ─────────────────────────────────────
            "adx": feat.adx,
            "adx_pos": feat.adx_pos,
            "adx_neg": feat.adx_neg,
            # ── Volume ─────────────────────────────────────────────
            "volume_ratio": feat.volume_ratio,
            "volume_trend_3": feat.volume_trend_3,
            # ── Candle structure ───────────────────────────────────
            "candle_body_pct": feat.candle_body_pct,
            "candle_upper_shadow": feat.candle_upper_shadow,
            "candle_lower_shadow": feat.candle_lower_shadow,
            # ── Market context ─────────────────────────────────────
            "fear_greed": feat.fear_greed,
            "funding_rate": feat.funding_rate,
            "oi_change": feat.oi_change,
            # ── Timing ─────────────────────────────────────────────
            "hour_utc": feat.hour_utc,
            "day_of_week": feat.day_of_week,
            # ── Trade outcome enrichment ───────────────────────────
            "actual_pnl_pct": feat.actual_pnl_pct,
            "max_favorable_excursion": feat.max_favorable_excursion,
            "max_adverse_excursion": feat.max_adverse_excursion,
            "time_to_result_hours": feat.time_to_result_hours,
        })
    return dataset


# ── Performance stats ─────────────────────────────────────────

@router.get("/performance")
async def performance_stats():
    from app.engine.performance import get_all_stats
    return get_all_stats()


@router.get("/performance/equity-curve")
async def performance_equity_curve():
    from app.engine.performance import equity_curve, portfolio_summary
    return {
        "summary": portfolio_summary(),
        "curve": equity_curve(),
    }


# ── Dedup state (debug) ───────────────────────────────────────

@router.get("/dedup/state")
async def dedup_state():
    return {"slots": get_state_summary()}


@router.delete("/dedup/state/{symbol}")
async def dedup_clear(symbol: str, timeframe: Optional[str] = None):
    clear_symbol(symbol.upper(), timeframe)
    return {"ok": True, "cleared": symbol.upper()}


# ── Orders ────────────────────────────────────────────────────

@router.get("/orders")
async def list_orders(
    signal_id: Optional[int] = None,
    limit: int = Query(50, le=200),
    session: Session = Depends(get_session),
):
    q = select(TradeOrder).order_by(desc(TradeOrder.created_at)).limit(limit)
    if signal_id is not None:
        q = q.where(TradeOrder.signal_id == signal_id)
    orders = session.exec(q).all()
    return [
        {
            "id": o.id,
            "signal_id": o.signal_id,
            "binance_order_id": o.binance_order_id,
            "symbol": o.symbol,
            "binance_symbol": o.binance_symbol,
            "side": o.side,
            "order_type": o.order_type,
            "role": o.role,
            "quantity": o.quantity,
            "price": o.price,
            "stop_price": o.stop_price,
            "status": o.status,
            "execution_mode": o.execution_mode,
            "error": o.error,
            "created_at": o.created_at.isoformat() + "Z",
        }
        for o in orders
    ]


# ── Health ────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {
        "status": "ok",
        "scheduler_running": scheduler.running,
        "ws_connections": len(manager.active),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ── Helpers ───────────────────────────────────────────────────

def _signal_to_dict(s: Signal) -> dict:
    return {
        "id": s.id,
        "created_at": s.created_at.isoformat() + "Z",
        "symbol": s.symbol,
        "direction": s.direction,
        "timeframe": s.timeframe,
        "risk_profile": s.risk_profile,
        "entry_price": s.entry_price,
        "entry_low": s.entry_low,
        "entry_high": s.entry_high,
        "tp1": s.tp1,
        "tp2": s.tp2,
        "sl": s.sl,
        "risk_reward": s.risk_reward,
        "confidence": s.confidence,
        "ta_score": s.ta_score,
        "pattern_score": s.pattern_score,
        "ml_score": s.ml_score,
        "context_score": s.context_score,
        "triggers": s.triggers,
        "rsi": s.rsi,
        "volume_ratio": s.volume_ratio,
        "funding_rate": s.funding_rate,
        "fear_greed": s.fear_greed,
        "leverage": s.leverage,
        "position_risk_pct": s.position_risk_pct,
        "breakeven_trigger": s.breakeven_trigger,
        "discord_sent": s.discord_sent,
        "result": s.result,
        "result_at": s.result_at.isoformat() + "Z" if s.result_at else None,
        "result_price": s.result_price,
        "tier": s.tier,
        "tp1_hit": s.tp1_hit,
        "tp1_hit_at": s.tp1_hit_at.isoformat() + "Z" if s.tp1_hit_at else None,
        "breakeven_sl": s.breakeven_sl,
    }
