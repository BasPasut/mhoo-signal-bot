from fastapi import WebSocket
from typing import List
import json
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WS connected. Total: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        logger.info(f"WS disconnected. Total: {len(self.active)}")

    async def broadcast(self, data: dict):
        payload = json.dumps(data)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)

    async def broadcast_signal(self, signal_dict: dict):
        await self.broadcast({"type": "signal", "data": signal_dict})

    async def broadcast_tp1_update(self, signal_id: int, breakeven_sl: float, tp1_hit_at: str):
        """Push tp1_hit state change so the dashboard moves the card immediately."""
        await self.broadcast({"type": "tp1_update", "data": {
            "id": signal_id,
            "tp1_hit": True,
            "tp1_hit_at": tp1_hit_at,
            "breakeven_sl": breakeven_sl,
        }})

    async def broadcast_status(self, message: str):
        await self.broadcast({"type": "status", "data": {"message": message}})


manager = ConnectionManager()
