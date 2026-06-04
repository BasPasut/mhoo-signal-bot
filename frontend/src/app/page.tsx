"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { useWebSocket, Signal } from "@/hooks/useWebSocket";
import { SignalCard } from "@/components/signals/SignalCard";
import { StatsBar } from "@/components/signals/StatsBar";
import { AnalyticsPanel, AnalyticsData } from "@/components/signals/AnalyticsPanel";
import { api } from "@/lib/api";
import clsx from "clsx";

// ── Glossary ──────────────────────────────────────────────────────────────────

const TERMS = [
  { term: "LONG",                   def: "Betting price goes up. You profit when it does." },
  { term: "SHORT",                  def: "Betting price goes down. You profit when it falls." },
  { term: "ALPHA / PRIME / SETUP",  def: "Signal strength. ALPHA ≥80% — strongest. PRIME 60–79% — solid. SETUP <60% — marginal, use smaller size." },
  { term: "Entry zone",             def: "Place your limit order anywhere in this range. Don't chase price outside it." },
  { term: "TP1",                    def: "Take-profit level 1. Exit 50% here, then move stop to entry — trade becomes risk-free." },
  { term: "TP2",                    def: "Take-profit level 2. Target for the remaining 50% after TP1. Not guaranteed." },
  { term: "SL",                     def: "Stop loss. Your exit if the trade goes wrong. Never remove it." },
  { term: "R/R",                    def: "Risk/Reward ratio. 1:1.5 = risk $1 to potentially make $1.50." },
  { term: "Leverage",               def: "Multiplies your position. 5× means $100 margin controls $500. Gains AND losses scale equally." },
  { term: "lev %",                  def: "Gain/loss as a % of your deposited margin when leverage is applied." },
  { term: "Confidence %",           def: "% of technical checks that passed. Built from 4H trend → 1H momentum → entry TF → market context." },
  { term: "RSI",                    def: "Momentum gauge 0–100. >70 = overheated. <30 = oversold." },
  { term: "Funding rate",           def: "Fee every 8h between longs and shorts. >0.1% = crowded long = bearish pressure." },
  { term: "FVG / OB",               def: "Smart Money Concepts. FVG = price gap left by large orders. OB = zone where institutions likely acted." },
];

function Glossary() {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-gray-900/50 transition-colors"
      >
        <span className="text-xs text-gray-600">Glossary</span>
        <span className="text-xs text-gray-700">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="bg-gray-900/50 border-t border-gray-800 px-4 py-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-3">
            {TERMS.map(g => (
              <div key={g.term}>
                <div className="text-xs font-semibold text-gray-400 mb-0.5">{g.term}</div>
                <div className="text-xs text-gray-600 leading-relaxed">{g.def}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [signals, setSignals]               = useState<Signal[]>([]);
  const [stats, setStats]                   = useState<any>(null);
  const [analytics, setAnalytics]           = useState<AnalyticsData | null>(null);
  const [statsLoading, setStatsLoading]     = useState(true);
  const [analyticsLoading, setAnalyticsLoading] = useState(true);
  const [maxOpenPositions, setMaxOpenPositions] = useState<number>(5);
  const [newIds, setNewIds]                 = useState<Set<number>>(new Set());
  const [scanning, setScanning]             = useState(false);
  const [scanMsg, setScanMsg]               = useState("");
  const [filter, setFilter]                 = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [prices, setPrices]                 = useState<Record<string, number>>({});
  const signalsRef                          = useRef<Signal[]>([]);

  const fetchPrices = useCallback(async () => {
    const openSymbols = [...new Set(signalsRef.current.filter(s => !s.result).map(s => s.symbol))];
    if (!openSymbols.length) return;
    const results = await Promise.allSettled(openSymbols.map(sym => api.price(sym)));
    setPrices(prev => {
      const next = { ...prev };
      results.forEach((r, i) => {
        if (r.status === "fulfilled") next[openSymbols[i]] = r.value.price;
      });
      return next;
    });
  }, []);

  useEffect(() => {
    api.signals({ open_only: true, limit: 50 }).then(s => { setSignals(s); signalsRef.current = s; fetchPrices(); }).catch(console.error);
    api.signalStats().then(setStats).catch(console.error).finally(() => setStatsLoading(false));
    api.analytics().then(setAnalytics).catch(console.error).finally(() => setAnalyticsLoading(false));
    api.config().then((c: any) => setMaxOpenPositions(c.max_open_positions ?? 5)).catch(console.error);

    const priceTimer = setInterval(fetchPrices, 60_000);
    const refreshTimer = setInterval(() => {
      api.signals({ open_only: true, limit: 50 }).then(s => { setSignals(s); signalsRef.current = s; fetchPrices(); }).catch(console.error);
      api.signalStats().then(setStats).catch(console.error);
      api.analytics().then(setAnalytics).catch(console.error);
    }, 300_000);
    return () => { clearInterval(priceTimer); clearInterval(refreshTimer); };
  }, [fetchPrices]);

  const onSignal = useCallback((s: Signal) => {
    setSignals(prev => { const next = [s, ...prev].slice(0, 100); signalsRef.current = next; return next; });
    setNewIds(prev => new Set(prev).add(s.id));
    setTimeout(() =>
      setNewIds(prev => { const n = new Set(prev); n.delete(s.id); return n; }),
      5000
    );
    api.signalStats().then(setStats).catch(console.error);
    api.analytics().then(setAnalytics).catch(console.error);
    fetchPrices();
  }, [fetchPrices]);

  useWebSocket(onSignal);

  const handleScanNow = async () => {
    setScanning(true);
    setScanMsg("Scanning…");
    try { await api.scanNow(); setScanMsg("Scan triggered"); }
    catch { setScanMsg("Scan failed"); }
    setTimeout(() => { setScanning(false); setScanMsg(""); }, 3000);
  };

  const filtered = filter === "ALL" ? signals : signals.filter(s => s.direction === filter);
  const openCount  = stats?.open ?? 0;
  const atPositionLimit = openCount >= maxOpenPositions;

  return (
    <div className="space-y-4 max-w-4xl mx-auto">

      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-3 pt-1">
        <div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <h1 className="text-base font-bold text-white">Live Positions</h1>
            <StatsBar stats={stats} loading={statsLoading} />
          </div>
          <p className="text-xs text-gray-700 mt-1">
            Binance Futures · 5 min scans
            {signals.length > 0 && (
              <span className="ml-2 text-gray-600">
                · <span className="text-sky-500 font-medium">{signals.length}</span> open
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2.5 shrink-0">
          {scanMsg && <span className="text-xs text-gray-500">{scanMsg}</span>}
          <button
            onClick={handleScanNow}
            disabled={scanning}
            className="btn-primary disabled:opacity-50 text-xs px-3 py-1.5"
          >
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>

      {/* ── Analytics panel ── */}
      <AnalyticsPanel data={analytics} loading={analyticsLoading} />

      {/* ── Position limit ── */}
      {atPositionLimit && (
        <div className="border border-yellow-900/30 rounded-lg px-4 py-2.5 flex items-start gap-2.5 bg-yellow-950/20">
          <span className="text-yellow-600 text-[10px] font-bold tracking-wider mt-0.5 shrink-0">AT LIMIT</span>
          <span className="text-xs text-yellow-900">
            {openCount}/{maxOpenPositions} positions open — new signals will queue until one resolves.
          </span>
        </div>
      )}

      {/* ── Direction filter ── */}
      <div className="flex items-center gap-1.5">
        {(["ALL", "LONG", "SHORT"] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={clsx(
              "px-3 py-1 rounded-full text-xs font-semibold transition-colors",
              filter === f
                ? "bg-gray-800 text-white"
                : "text-gray-600 hover:text-gray-300 hover:bg-gray-800/60"
            )}
          >
            {f}
          </button>
        ))}
        <span className="text-xs text-gray-700 ml-1">{filtered.length}</span>
      </div>

      {/* ── Signal grid ── */}
      {filtered.length === 0 ? (
        <div className="border border-gray-800 rounded-lg text-center py-16">
          <p className="text-sm text-gray-600">No open positions</p>
          <p className="text-xs text-gray-700 mt-1">
            {signals.length === 0
              ? "Hit Scan now to check for setups"
              : `All ${filter} signals filtered — switch to ALL`}
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
          {filtered.map(s => (
            <SignalCard key={s.id} signal={s} isNew={newIds.has(s.id)} livePrice={prices[s.symbol] ?? null} />
          ))}
        </div>
      )}

      {/* ── Glossary ── */}
      <Glossary />

    </div>
  );
}
