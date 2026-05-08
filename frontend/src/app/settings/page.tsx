"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import clsx from "clsx";

const TIMEFRAME_OPTIONS = ["5m", "15m", "1h", "4h", "1d"];
const RISK_PROFILES = ["conservative", "balanced", "aggressive"] as const;

export default function SettingsPage() {
  const [config, setConfig] = useState<any>(null);
  const [watchlist, setWatchlist] = useState("");
  const [riskProfile, setRiskProfile] = useState("balanced");
  const [timeframes, setTimeframes] = useState<string[]>(["15m", "1h"]);
  const [scanInterval, setScanInterval] = useState(300);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.config().then((c) => {
      setConfig(c);
      setWatchlist(c.watchlist.join(", "));
      setRiskProfile(c.risk_profile);
      setTimeframes(c.timeframes);
      setScanInterval(c.scan_interval);
    });
  }, []);

  const toggleTf = (tf: string) => {
    setTimeframes((prev) =>
      prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf]
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const syms = watchlist.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      await api.updateConfig({
        watchlist: syms,
        risk_profile: riskProfile,
        scan_interval: scanInterval,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      alert("Save failed. Check backend connection.");
    } finally {
      setSaving(false);
    }
  };

  if (!config) {
    return <div className="text-gray-500 text-sm animate-pulse">Loading settings...</div>;
  }

  return (
    <div className="max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <p className="text-sm text-gray-500 mt-0.5">Configure your signal bot</p>
      </div>

      {/* Watchlist */}
      <div className="card space-y-2">
        <h2 className="font-semibold text-white">Watchlist</h2>
        <p className="text-xs text-gray-500">Comma-separated Binance USDT-M perpetual base assets</p>
        <input
          className="input"
          value={watchlist}
          onChange={(e) => setWatchlist(e.target.value)}
          placeholder="BTC, ETH, XRP, SOL"
        />
        <p className="text-xs text-gray-600">
          Current: {watchlist.split(",").filter(Boolean).length} symbol(s)
        </p>
      </div>

      {/* Risk profile */}
      <div className="card space-y-3">
        <h2 className="font-semibold text-white">Risk profile</h2>
        <div className="grid grid-cols-3 gap-2">
          {RISK_PROFILES.map((p) => (
            <button
              key={p}
              onClick={() => setRiskProfile(p)}
              className={clsx(
                "py-2 rounded-lg text-sm font-medium capitalize border transition-all",
                riskProfile === p
                  ? "bg-emerald-600/20 border-emerald-500 text-emerald-400"
                  : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500"
              )}
            >
              {p}
            </button>
          ))}
        </div>
        <div className="text-xs text-gray-500 space-y-0.5">
          <div>Conservative: fires at {config.min_confidence.conservative}%+ confidence</div>
          <div>Balanced: fires at {config.min_confidence.balanced}%+ confidence</div>
          <div>Aggressive: fires at {config.min_confidence.aggressive}%+ confidence</div>
        </div>
      </div>

      {/* Timeframes */}
      <div className="card space-y-3">
        <h2 className="font-semibold text-white">Timeframes</h2>
        <div className="flex flex-wrap gap-2">
          {TIMEFRAME_OPTIONS.map((tf) => (
            <button
              key={tf}
              onClick={() => toggleTf(tf)}
              className={clsx(
                "px-3 py-1.5 rounded-lg text-sm font-mono border transition-all",
                timeframes.includes(tf)
                  ? "bg-emerald-600/20 border-emerald-500 text-emerald-400"
                  : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500"
              )}
            >
              {tf}
            </button>
          ))}
        </div>
        <p className="text-xs text-gray-500">
          More timeframes = more signals but longer scan time
        </p>
      </div>

      {/* Scan interval */}
      <div className="card space-y-2">
        <h2 className="font-semibold text-white">Scan interval</h2>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={60}
            max={3600}
            step={60}
            value={scanInterval}
            onChange={(e) => setScanInterval(Number(e.target.value))}
            className="flex-1 accent-emerald-500"
          />
          <span className="text-sm text-gray-300 w-20 text-right">
            {scanInterval >= 3600
              ? `${scanInterval / 3600}h`
              : `${scanInterval / 60}m`}
          </span>
        </div>
      </div>

      {/* Save */}
      <button
        onClick={handleSave}
        disabled={saving}
        className="btn-primary w-full disabled:opacity-50"
      >
        {saving ? "Saving..." : saved ? "✓ Saved" : "Save settings"}
      </button>
    </div>
  );
}
