from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlmodel import Session, select, desc
from typing import Optional
from datetime import datetime, timedelta
import json

from app.models.db import Signal, get_session
from app.core.ws import manager
from app.core.config_store import (
    get_watchlist, set_watchlist, get_risk_profile, set_risk_profile,
    get_timeframes, get_scan_interval,
)
from app.core.settings import settings
from app.scheduler.runner import run_now, update_interval, scheduler
from app.engine import binance

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
    limit: int = Query(50, le=200),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    q = select(Signal).order_by(desc(Signal.created_at)).offset(offset).limit(limit)
    if symbol:
        q = q.where(Signal.symbol == symbol.upper())
    if direction:
        q = q.where(Signal.direction == direction.upper())
    signals = session.exec(q).all()
    return [_signal_to_dict(s) for s in signals]


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: int, session: Session = Depends(get_session)):
    s = session.get(Signal, signal_id)
    if not s:
        raise HTTPException(404, "Signal not found")
    return _signal_to_dict(s)


@router.get("/signals/stats/summary")
async def signal_stats(session: Session = Depends(get_session)):
    signals = session.exec(select(Signal)).all()
    total = len(signals)
    wins = sum(1 for s in signals if s.result == "win")
    losses = sum(1 for s in signals if s.result == "loss")
    longs = sum(1 for s in signals if s.direction == "LONG")
    shorts = sum(1 for s in signals if s.direction == "SHORT")
    avg_confidence = sum(s.confidence for s in signals) / total if total else 0
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / (wins + losses) * 100, 1) if wins + losses else 0,
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
        "min_confidence": {
            "conservative": settings.min_confidence_conservative,
            "balanced": settings.min_confidence_balanced,
            "aggressive": settings.min_confidence_aggressive,
        },
    }


@router.patch("/config")
async def update_config(body: dict):
    if "watchlist" in body:
        syms = [s.strip().upper() for s in body["watchlist"] if s.strip()]
        if not syms:
            raise HTTPException(400, "Watchlist cannot be empty")
        set_watchlist(syms)

    if "risk_profile" in body:
        p = body["risk_profile"]
        if p not in ("conservative", "balanced", "aggressive"):
            raise HTTPException(400, "Invalid risk profile")
        set_risk_profile(p)

    if "scan_interval" in body:
        secs = int(body["scan_interval"])
        if secs < 60:
            raise HTTPException(400, "scan_interval must be ≥ 60 seconds")
        from app.core.config_store import set_config
        set_config("scan_interval", str(secs))
        update_interval(secs)

    return {"ok": True, "config": await get_config_endpoint()}


# ── Scan trigger ──────────────────────────────────────────────

@router.post("/scan/now")
async def trigger_scan():
    await run_now()
    return {"ok": True, "message": "Scan triggered"}


# ── Price endpoint ────────────────────────────────────────────

@router.get("/price/{symbol}")
async def get_price(symbol: str):
    try:
        price = await binance.get_current_price(symbol.upper())
        funding = await binance.get_funding_rate(symbol.upper())
        return {"symbol": symbol.upper(), "price": price, "funding_rate": funding}
    except Exception as e:
        raise HTTPException(502, str(e))


# ── Health ────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {
        "status": "ok",
        "scheduler_running": scheduler.running,
        "ws_connections": len(manager.active),
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── Helpers ───────────────────────────────────────────────────

def _signal_to_dict(s: Signal) -> dict:
    return {
        "id": s.id,
        "created_at": s.created_at.isoformat(),
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
        "discord_sent": s.discord_sent,
        "result": s.result,
    }
