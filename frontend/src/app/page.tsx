"use client";
import { useState, useEffect, useCallback } from "react";
import { useWebSocket, Signal } from "@/hooks/useWebSocket";
import { SignalCard } from "@/components/signals/SignalCard";
import { StatsBar } from "@/components/signals/StatsBar";
import { api } from "@/lib/api";

export default function DashboardPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [newIds, setNewIds] = useState<Set<number>>(new Set());
  const [scanning, setScanning] = useState(false);
  const [filter, setFilter] = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [status, setStatus] = useState("");

  useEffect(() => {
    api.signals({ limit: 20 }).then(setSignals).catch(console.error);
    api.signalStats().then(setStats).catch(console.error);
  }, []);

  const onSignal = useCallback((s: Signal) => {
    setSignals((prev) => [s, ...prev].slice(0, 100));
    setNewIds((prev) => new Set(prev).add(s.id));
    setTimeout(() => setNewIds((prev) => { const n = new Set(prev); n.delete(s.id); return n; }), 4000);
    api.signalStats().then(setStats).catch(console.error);
  }, []);

  useWebSocket(onSignal);

  const handleScanNow = async () => {
    setScanning(true);
    setStatus("Scan triggered...");
    try {
      await api.scanNow();
    } catch (e) {
      setStatus("Scan failed");
    } finally {
      setTimeout(() => setScanning(false), 2000);
    }
  };

  const filtered = filter === "ALL" ? signals : signals.filter((s) => s.direction === filter);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-0.5">Live trading signals</p>
        </div>
        <div className="flex items-center gap-2">
          {status && <span className="text-xs text-gray-400">{status}</span>}
          <button
            onClick={handleScanNow}
            disabled={scanning}
            className="btn-primary disabled:opacity-50"
          >
            {scanning ? "Scanning..." : "Scan now"}
          </button>
        </div>
      </div>

      {/* Stats */}
      <StatsBar stats={stats} />

      {/* Filter */}
      <div className="flex items-center gap-2">
        {(["ALL", "LONG", "SHORT"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={
              filter === f
                ? "px-3 py-1 rounded-full text-xs font-semibold bg-gray-700 text-white"
                : "px-3 py-1 rounded-full text-xs font-semibold text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            }
          >
            {f}
          </button>
        ))}
        <span className="text-xs text-gray-600 ml-2">{filtered.length} signals</span>
      </div>

      {/* Signal grid */}
      {filtered.length === 0 ? (
        <div className="card text-center py-16 text-gray-600">
          <div className="text-4xl mb-3">📡</div>
          <div className="text-sm">No signals yet. Click &quot;Scan now&quot; to run the first analysis.</div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((s) => (
            <SignalCard key={s.id} signal={s} isNew={newIds.has(s.id)} />
          ))}
        </div>
      )}
    </div>
  );
}
