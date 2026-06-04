"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import clsx from "clsx";

const SIGNAL_TIERS = [
  { id: "ALPHA", label: "ALPHA", range: "≥ 80%", color: "emerald", desc: "Strongest setups — highest confluence" },
  { id: "PRIME", label: "PRIME", range: "60–79%", color: "yellow",  desc: "Solid setups — good confluence" },
  { id: "SETUP", label: "SETUP", range: "< 60%",  color: "orange",  desc: "Marginal setups — weaker confluence" },
] as const;
const PRIORITY_BIASES = ["Highest Confidence", "Lowest Risk"] as const;

export default function SettingsPage() {
  const [config, setConfig] = useState<any>(null);
  const [watchlist, setWatchlist] = useState("");
  const [signalTiers, setSignalTiers] = useState<string[]>(["ALPHA", "PRIME", "SETUP"]);
  const [scanInterval, setScanInterval] = useState(300);
  const [maxOpenPositions, setMaxOpenPositions] = useState(5);
  const [priorityBias, setPriorityBias] = useState<"Highest Confidence" | "Lowest Risk">("Highest Confidence");
  const [executionMode, setExecutionMode] = useState<"disabled" | "testnet" | "live">("disabled");
  const [startingBalance, setStartingBalance] = useState<number>(10000);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"ok" | "err" | null>(null);

  useEffect(() => {
    api.config()
      .then((c) => {
        setConfig(c);
        setWatchlist(c.watchlist.join(", "));
        setSignalTiers(c.signal_tiers ?? ["ALPHA", "PRIME", "SETUP"]);
        setScanInterval(c.scan_interval);
        setMaxOpenPositions(c.max_open_positions ?? 5);
        setPriorityBias(c.priority_bias ?? "Highest Confidence");
        setExecutionMode(c.execution_mode ?? "disabled");
        setStartingBalance(c.starting_balance ?? 10000);
      })
      .catch((err) => {
        setLoadError(`Cannot connect to backend: ${err.message}`);
      });
  }, []);

  const handleTestDiscord = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      await api.testDiscord();
      setTestResult("ok");
    } catch {
      setTestResult("err");
    } finally {
      setTesting(false);
      setTimeout(() => setTestResult(null), 4000);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const syms = watchlist.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      await api.updateConfig({
        watchlist: syms,
        signal_tiers: signalTiers,
        scan_interval: scanInterval,
        max_open_positions: maxOpenPositions,
        priority_bias: priorityBias,
        execution_mode: executionMode,
        starting_balance: startingBalance,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e: any) {
      setSaveError(e?.message ?? "Connection failed — is the backend running?");
    } finally {
      setSaving(false);
    }
  };

  if (loadError) {
    return (
      <div className="rounded-lg bg-red-900/30 border border-red-500/40 px-4 py-3 text-sm text-red-400">
        {loadError}
      </div>
    );
  }

  if (!config) {
    return <div className="text-gray-500 text-sm animate-pulse">Loading settings...</div>;
  }

  return (
    <div className="max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <p className="text-sm text-gray-500 mt-0.5">Configure your Mhoo Signal Bot</p>
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

      {/* Signal tiers */}
      <div className="card space-y-3">
        <div>
          <h2 className="font-semibold text-white">Signal tiers</h2>
          <p className="text-xs text-gray-500 mt-0.5">Choose which confidence grades to receive. Select at least one.</p>
        </div>
        <div className="space-y-2">
          {SIGNAL_TIERS.map((t) => {
            const checked = signalTiers.includes(t.id);
            const borderColor = t.color === "emerald" ? "border-emerald-500" : t.color === "yellow" ? "border-yellow-500" : "border-orange-500";
            const bgColor    = t.color === "emerald" ? "bg-emerald-600/10" : t.color === "yellow" ? "bg-yellow-600/10" : "bg-orange-600/10";
            const textColor  = t.color === "emerald" ? "text-emerald-400" : t.color === "yellow" ? "text-yellow-400" : "text-orange-400";
            const dotColor   = t.color === "emerald" ? "bg-emerald-500" : t.color === "yellow" ? "bg-yellow-500" : "bg-orange-500";
            return (
              <button
                key={t.id}
                onClick={() => {
                  if (checked && signalTiers.length === 1) return; // keep at least one
                  setSignalTiers(prev =>
                    checked ? prev.filter(x => x !== t.id) : [...prev, t.id]
                  );
                }}
                className={clsx(
                  "w-full flex items-center gap-3 px-4 py-3 rounded-lg border text-left transition-all",
                  checked ? `${bgColor} ${borderColor}` : "bg-gray-800 border-gray-700 hover:border-gray-500"
                )}
              >
                {/* Checkbox */}
                <div className={clsx(
                  "w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-all",
                  checked ? `${borderColor} ${bgColor}` : "border-gray-600"
                )}>
                  {checked && <div className={clsx("w-2 h-2 rounded-sm", dotColor)} />}
                </div>
                {/* Label */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={clsx("text-sm font-bold", checked ? textColor : "text-gray-400")}>{t.label}</span>
                    <span className="text-xs text-gray-600">{t.range}</span>
                  </div>
                  <p className="text-xs text-gray-600 mt-0.5">{t.desc}</p>
                </div>
              </button>
            );
          })}
        </div>
        {signalTiers.length === 0 && (
          <p className="text-xs text-red-400">Select at least one tier.</p>
        )}
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

      {/* Position limits */}
      <div className="card space-y-4">
        <div>
          <h2 className="font-semibold text-white">Position limits</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Limits how many open trades can be active at once. Each scan still checks every coin — new signals are held back only when this limit is already reached.
          </p>
        </div>

        <div className="space-y-2">
          <label className="text-xs text-gray-400">Max open positions</label>
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={1}
              max={10}
              step={1}
              value={maxOpenPositions}
              onChange={(e) => setMaxOpenPositions(Number(e.target.value))}
              className="flex-1 accent-emerald-500"
            />
            <span className="text-sm text-gray-300 w-8 text-right">{maxOpenPositions}</span>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs text-gray-400">Priority bias</label>
          <div className="grid grid-cols-2 gap-2">
            {PRIORITY_BIASES.map((b) => (
              <button
                key={b}
                onClick={() => setPriorityBias(b)}
                className={clsx(
                  "py-2 rounded-lg text-sm font-medium border transition-all",
                  priorityBias === b
                    ? "bg-emerald-600/20 border-emerald-500 text-emerald-400"
                    : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500"
                )}
              >
                {b}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-600">
            {priorityBias === "Highest Confidence"
              ? "Sends the signals with the highest confidence score first. Ties broken by Tier 1 > 2 > 3."
              : "Sends the signals with the tightest stop-loss first, minimising per-trade risk."}
          </p>
        </div>
      </div>

      {/* Auto-execution */}
      <div className="card space-y-4">
        <div>
          <h2 className="font-semibold text-white">Auto-execution</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Automatically place Binance Futures orders when a signal fires. API keys must be set in your <code className="text-gray-400">.env</code> file.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {/* Disabled */}
          <div className="flex flex-col gap-1">
            <button
              onClick={() => setExecutionMode("disabled")}
              className={clsx(
                "py-2 rounded-lg text-sm font-medium border transition-all",
                executionMode === "disabled"
                  ? "bg-gray-600/30 border-gray-400 text-gray-200"
                  : "bg-gray-800 border-gray-700 text-gray-500 hover:border-gray-500"
              )}
            >
              Disabled
            </button>
            <span className="text-center text-xs text-gray-600">—</span>
          </div>
          {/* Testnet */}
          <div className="flex flex-col gap-1">
            <button
              onClick={() => setExecutionMode("testnet")}
              className={clsx(
                "py-2 rounded-lg text-sm font-medium border transition-all",
                executionMode === "testnet"
                  ? "bg-sky-600/20 border-sky-500 text-sky-400"
                  : "bg-gray-800 border-gray-700 text-gray-500 hover:border-gray-500"
              )}
            >
              Testnet
            </button>
            <span className={clsx(
              "text-center text-xs px-1 py-0.5 rounded",
              config?.execution_keys_configured?.testnet
                ? "text-emerald-400"
                : "text-gray-600"
            )}>
              {config?.execution_keys_configured?.testnet ? "Keys set" : "No keys"}
            </span>
          </div>
          {/* Live */}
          <div className="flex flex-col gap-1">
            <button
              onClick={() => setExecutionMode("live")}
              className={clsx(
                "py-2 rounded-lg text-sm font-medium border transition-all",
                executionMode === "live"
                  ? "bg-rose-600/20 border-rose-500 text-rose-400"
                  : "bg-gray-800 border-gray-700 text-gray-500 hover:border-gray-500"
              )}
            >
              Live
            </button>
            <span className={clsx(
              "text-center text-xs px-1 py-0.5 rounded",
              config?.execution_keys_configured?.live
                ? "text-emerald-400"
                : "text-gray-600"
            )}>
              {config?.execution_keys_configured?.live ? "Keys set" : "No keys"}
            </span>
          </div>
        </div>
        {executionMode === "live" && (
          <div className="rounded-lg bg-rose-900/30 border border-rose-500/40 px-3 py-2 text-xs text-rose-400">
            Real money will be used. Ensure your API key has Futures trading permission only, NOT withdrawal permission.
          </div>
        )}
      </div>

      {/* Starting balance */}
      <div className="card space-y-3">
        <div>
          <h2 className="font-semibold text-white">Portfolio starting balance</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Your initial capital in USDT. Used by the Performance page to simulate real P&amp;L — equity curve, total return %, and drawdown are all calculated from this base.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-400">$</span>
          <input
            type="number"
            min={1}
            step={100}
            value={startingBalance}
            onChange={(e) => setStartingBalance(Math.max(1, Number(e.target.value)))}
            className="input flex-1"
            placeholder="10000"
          />
          <span className="text-sm text-gray-500">USDT</span>
        </div>
        <p className="text-xs text-gray-600">
          Current: <span className="text-white font-medium">${startingBalance.toLocaleString()}</span> USDT
        </p>
      </div>

      {/* Discord test */}
      <div className="card space-y-2">
        <h2 className="font-semibold text-white">Discord</h2>
        <p className="text-xs text-gray-500">Send a test message to verify the bot connection.</p>
        <button
          onClick={handleTestDiscord}
          disabled={testing}
          className={clsx(
            "w-full py-2 rounded-lg text-sm font-medium border transition-all disabled:opacity-50",
            testResult === "ok"
              ? "bg-emerald-600/20 border-emerald-500 text-emerald-400"
              : testResult === "err"
              ? "bg-red-600/20 border-red-500 text-red-400"
              : "bg-gray-800 border-gray-700 text-gray-300 hover:border-gray-500"
          )}
        >
          {testing ? "Sending..." : testResult === "ok" ? "✓ Sent to Discord" : testResult === "err" ? "✗ Failed — check logs" : "Send test message"}
        </button>
      </div>

      {/* Save */}
      {saveError && (
        <div className="rounded-lg bg-red-900/30 border border-red-500/40 px-3 py-2 text-sm text-red-400">
          {saveError}
        </div>
      )}
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
