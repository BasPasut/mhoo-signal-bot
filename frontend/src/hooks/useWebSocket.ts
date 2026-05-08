"use client";
import { useEffect, useRef, useState, useCallback } from "react";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

type WsMessage =
  | { type: "signal"; data: Signal }
  | { type: "status"; data: { message: string } };

export interface Signal {
  id: number;
  created_at: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  timeframe: string;
  risk_profile: string;
  entry_price: number;
  entry_low: number;
  entry_high: number;
  tp1: number;
  tp2: number;
  sl: number;
  risk_reward: number;
  confidence: number;
  ta_score: number;
  pattern_score: number;
  ml_score: number;
  context_score: number;
  triggers: { label: string; dir: string; w: number }[];
  rsi?: number;
  volume_ratio?: number;
  funding_rate?: number;
  fear_greed?: number;
  discord_sent: boolean;
  result?: string;
}

let _connected = false;
const _listeners = new Set<(c: boolean) => void>();

export function useWsStatus() {
  const [connected, setConnected] = useState(_connected);
  useEffect(() => {
    _listeners.add(setConnected);
    return () => { _listeners.delete(setConnected); };
  }, []);
  return connected;
}

function notifyStatus(c: boolean) {
  _connected = c;
  _listeners.forEach((fn) => fn(c));
}

export function useWebSocket(onSignal: (s: Signal) => void) {
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const onSignalRef = useRef(onSignal);
  onSignalRef.current = onSignal;

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return;
    const socket = new WebSocket(`${WS_URL}/api/ws`);
    ws.current = socket;

    socket.onopen = () => { notifyStatus(true); };
    socket.onclose = () => {
      notifyStatus(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };
    socket.onerror = () => socket.close();
    socket.onmessage = (e) => {
      try {
        const msg: WsMessage = JSON.parse(e.data);
        if (msg.type === "signal") onSignalRef.current(msg.data);
      } catch {}
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);
}
