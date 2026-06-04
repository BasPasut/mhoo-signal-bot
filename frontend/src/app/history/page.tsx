"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { SignalCard } from "@/components/signals/SignalCard";
import { StatsBar } from "@/components/signals/StatsBar";
import { Signal } from "@/hooks/useWebSocket";

export default function HistoryPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState("");
  const [page, setPage] = useState(0);
  const PAGE = 18;

  const load = async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { limit: PAGE, offset: page * PAGE };
      if (symbol) params.symbol = symbol.toUpperCase();
      if (direction) params.direction = direction;
      const [sigs, st] = await Promise.all([api.signals(params), api.signalStats()]);
      setSignals(sigs);
      setStats(st);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [page, symbol, direction]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <h1 className="text-base font-bold text-white">History</h1>
            <StatsBar stats={stats} />
          </div>
          <p className="text-xs text-gray-700 mt-1">All past signals</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            className="input w-28"
            placeholder="Symbol"
            value={symbol}
            onChange={(e) => { setPage(0); setSymbol(e.target.value); }}
          />
          <select
            className="input w-28"
            value={direction}
            onChange={(e) => { setPage(0); setDirection(e.target.value); }}
          >
            <option value="">All</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
          </select>
        </div>
      </div>

      {loading ? (
        <div className="text-gray-500 text-sm animate-pulse">Loading...</div>
      ) : signals.length === 0 ? (
        <div className="card text-center py-16 text-gray-600 text-sm">No signals match your filters.</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
          {signals.map((s) => <SignalCard key={s.id} signal={s} />)}
        </div>
      )}

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={page === 0}
          className="btn-ghost disabled:opacity-30"
        >
          ← Previous
        </button>
        <span className="text-xs text-gray-500">Page {page + 1}</span>
        <button
          onClick={() => setPage((p) => p + 1)}
          disabled={signals.length < PAGE}
          className="btn-ghost disabled:opacity-30"
        >
          Next →
        </button>
      </div>
    </div>
  );
}
