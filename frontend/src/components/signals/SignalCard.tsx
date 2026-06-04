"use client";
import { Signal } from "@/hooks/useWebSocket";
import { api } from "@/lib/api";
import clsx from "clsx";
import { useState, memo } from "react";

// ── Utilities ─────────────────────────────────────────────────────────────────

function fmt(v: number): string {
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function fmtDuration(a: string, b: string): string {
  const ms = new Date(b).getTime() - new Date(a).getTime();
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function tier(conf: number) {
  if (conf >= 80) return { label: "ALPHA", color: "text-emerald-400" };
  if (conf >= 60) return { label: "PRIME", color: "text-yellow-400" };
  return { label: "SETUP", color: "text-orange-400" };
}

function plainReason(raw: string): string {
  const l = raw.toLowerCase();
  if (
    l.includes("outside kill zone") || l.includes("no ob/fvg") ||
    l.includes("regime cap") || l.includes("momentum dedup") ||
    l.includes("correlated") || l.includes("structure-less")
  ) return "";
  if (l.includes("multi-tf"))     return "Multiple timeframes confirm direction";
  if (l.includes("ema200") && (l.includes("bull") || l.includes("price >"))) return "4H trend bullish";
  if (l.includes("ema200") && (l.includes("bear") || l.includes("price <"))) return "4H trend bearish";
  if (l.includes("macd") && l.includes("bull"))           return "1H momentum turning up";
  if (l.includes("macd") && l.includes("bear"))           return "1H momentum turning down";
  if (l.includes("bb squeeze") || l.includes("bb band"))  return "Volatility breakout";
  if (l.includes("hybrid l3") || (l.includes("rsi") && l.includes("fvg"))) return "RSI at supply/demand zone";
  if (l.includes("fvg"))                                  return "Fair value gap (high-prob entry)";
  if (l.includes("order block"))                          return "Institutional order block";
  if (l.includes("kill zone") && l.includes("+"))         return "Active session (London / NY)";
  if (l.includes("liquidity sweep"))                      return "Liquidity sweep → reversal setup";
  if (l.includes("vwap"))                                 return "Price on right side of VWAP";
  if (l.includes("downtrend structure"))                  return "Lower highs / lower lows confirmed";
  if (l.includes("breakout above"))                       return "Breaking above resistance";
  if (l.includes("breakdown below"))                      return "Breaking below support";
  if (l.includes("evening star"))                         return "Bearish reversal candle";
  if (l.includes("morning star"))                         return "Bullish reversal candle";
  if (l.includes("adx accelerating"))                     return "Trend strength accelerating";
  if (l.includes("ema stack"))                            return "Short-term EMAs confirm trend";
  return raw.split("|")[0].split("—")[0].trim();
}

function fmtAge(createdAt: string): string {
  const ms = Date.now() - new Date(createdAt).getTime();
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return h > 0 ? `${h}h ${m}m ago` : `${m}m ago`;
}

function CopyBtn({ label, value }: { label: string; value: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(value);
        setOk(true);
        setTimeout(() => setOk(false), 1500);
      }}
      className="hover:text-gray-300 transition-colors"
    >
      {ok ? `✓ ${label}` : label}
    </button>
  );
}

// ── P&L progress bar ──────────────────────────────────────────────────────────
// Spans SL → entry → TP1 with a dot showing current price position.

function PnlBar({
  pnl,
  tp1Gain,
  slLoss,
}: {
  pnl: number;
  tp1Gain: number;
  slLoss: number;
}) {
  const total     = slLoss + tp1Gain;
  const entryPct  = (slLoss / total) * 100;           // where entry sits on 0-100 scale
  const currentPct = Math.max(1, Math.min(99, ((pnl + slLoss) / total) * 100));
  const isProfit   = pnl >= 0;

  return (
    <div className="mt-2.5 mb-1">
      {/* Track */}
      <div className="relative h-1 bg-gray-800 rounded-full">
        {/* Filled region: entry → current */}
        {isProfit ? (
          <div
            className="absolute inset-y-0 bg-emerald-600/70 rounded-full"
            style={{ left: `${entryPct}%`, width: `${Math.max(0, currentPct - entryPct)}%` }}
          />
        ) : (
          <div
            className="absolute inset-y-0 bg-red-700/70 rounded-full"
            style={{ left: `${Math.max(0, currentPct)}%`, width: `${entryPct - Math.max(0, currentPct)}%` }}
          />
        )}
        {/* Entry tick */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-px h-2.5 bg-gray-500"
          style={{ left: `${entryPct}%` }}
        />
        {/* Current price dot */}
        <div
          className={clsx(
            "absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full border-2 shadow",
            isProfit
              ? "bg-emerald-400 border-emerald-700"
              : "bg-red-400 border-red-800"
          )}
          style={{ left: `calc(${currentPct}% - 5px)` }}
        />
      </div>
      {/* Labels */}
      <div className="flex justify-between mt-1 text-[10px] text-gray-700">
        <span>SL −{slLoss.toFixed(1)}%</span>
        <span className="text-gray-600">entry</span>
        <span>TP1 +{tp1Gain.toFixed(1)}%</span>
      </div>
    </div>
  );
}

// ── Manual override panel ─────────────────────────────────────────────────────

// Tier leverage caps: tier 1 → 20x, tier 2 → 10x, tier 3 → 5x, unknown → 20x
function maxLev(tier?: number | null) {
  if (tier === 1) return 20;
  if (tier === 2) return 10;
  if (tier === 3) return 5;
  return 20;
}

function leveragePresets(tier?: number | null) {
  const cap = maxLev(tier);
  return [2, 5, 10, 15, 20].filter(v => v <= cap);
}

function ManualOverride({
  signal,
  onUpdate,
}: {
  signal: Signal;
  onUpdate: (u: Partial<Signal>) => void;
}) {
  const [leverage, setLeverage]   = useState<number>(signal.leverage ?? 1);
  const [tp1, setTp1]             = useState<string>(String(signal.tp1));
  const [tp2, setTp2]             = useState<string>(signal.tp2 > 0 ? String(signal.tp2) : "");
  const [saving, setSaving]       = useState(false);
  const [saved, setSaved]         = useState(false);
  const [error, setError]         = useState("");

  const presets = leveragePresets(signal.tier);
  const isLong  = signal.direction === "LONG";

  // Live R/R preview from current TP1 input
  const tp1Num  = parseFloat(tp1);
  const slDist  = Math.abs(signal.entry_price - signal.sl);
  const tpDist  = !isNaN(tp1Num) ? Math.abs(tp1Num - signal.entry_price) : 0;
  const previewRR = slDist > 0 && tpDist > 0 ? (tpDist / slDist).toFixed(2) : null;
  const previewTp1Gain = !isNaN(tp1Num) && signal.entry_price > 0
    ? ((isLong ? tp1Num - signal.entry_price : signal.entry_price - tp1Num) / signal.entry_price * 100)
    : null;

  const hasChanges =
    leverage !== (signal.leverage ?? 1) ||
    parseFloat(tp1) !== signal.tp1 ||
    (tp2 !== "" && parseFloat(tp2) !== signal.tp2);

  async function handleSave() {
    setError("");
    const body: { leverage?: number; tp1?: number; tp2?: number } = {};
    if (leverage !== (signal.leverage ?? 1)) body.leverage = leverage;
    const tp1f = parseFloat(tp1);
    const tp2f = parseFloat(tp2);
    if (!isNaN(tp1f) && tp1f !== signal.tp1) body.tp1 = tp1f;
    if (tp2 !== "" && !isNaN(tp2f) && tp2f !== signal.tp2) body.tp2 = tp2f;
    if (Object.keys(body).length === 0) return;

    setSaving(true);
    try {
      const res = await api.updateSignal(signal.id, body);
      onUpdate({ leverage: res.leverage, tp1: res.tp1, tp2: res.tp2, risk_reward: res.risk_reward });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e.message ?? "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-gray-700 font-semibold uppercase tracking-wider">Manual Override</p>

      {/* ── Leverage ── */}
      <div>
        <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
          {presets.map(v => (
            <button
              key={v}
              onClick={() => setLeverage(v)}
              className={clsx(
                "px-2 py-0.5 rounded text-xs font-mono font-semibold transition-colors",
                leverage === v
                  ? "bg-yellow-500/20 text-yellow-400 border border-yellow-600/50"
                  : "bg-gray-800 text-gray-500 border border-gray-700 hover:text-gray-300 hover:border-gray-600"
              )}
            >
              {v}×
            </button>
          ))}
          {/* Custom input */}
          <div className="flex items-center gap-1">
            <input
              type="number"
              min={1}
              max={maxLev(signal.tier)}
              value={leverage}
              onChange={e => setLeverage(Math.min(maxLev(signal.tier), Math.max(1, parseInt(e.target.value) || 1)))}
              className="w-14 bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-xs font-mono text-gray-300 text-center focus:outline-none focus:border-gray-500"
            />
            <span className="text-xs text-gray-700">×</span>
          </div>
        </div>
        <p className="text-[10px] text-gray-700">
          Max for this tier: {maxLev(signal.tier)}×
          {leverage !== (signal.leverage ?? 1) && (
            <span className="text-yellow-700 ml-1.5">
              lev PnL preview: {previewTp1Gain !== null ? `TP1 ${previewTp1Gain >= 0 ? "+" : ""}${(previewTp1Gain * leverage).toFixed(1)}%` : "—"}
            </span>
          )}
        </p>
      </div>

      {/* ── TP inputs ── */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-600 w-8 shrink-0">TP1</span>
          <input
            type="number"
            step="any"
            value={tp1}
            onChange={e => setTp1(e.target.value)}
            className="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs font-mono text-gray-200 focus:outline-none focus:border-gray-500"
            placeholder={String(signal.tp1)}
          />
          {previewTp1Gain !== null && (
            <span className={clsx(
              "text-[11px] tabular-nums shrink-0",
              previewTp1Gain >= 0 ? "text-emerald-600" : "text-red-600"
            )}>
              {previewTp1Gain >= 0 ? "+" : ""}{previewTp1Gain.toFixed(2)}%
              {previewRR && <span className="text-gray-700 ml-1">R{previewRR}</span>}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-600 w-8 shrink-0">TP2</span>
          <input
            type="number"
            step="any"
            value={tp2}
            onChange={e => setTp2(e.target.value)}
            className="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs font-mono text-gray-200 focus:outline-none focus:border-gray-500"
            placeholder={signal.tp2 > 0 ? String(signal.tp2) : "optional"}
          />
        </div>
      </div>

      {/* ── Save button ── */}
      <div className="flex items-center gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !hasChanges}
          className={clsx(
            "px-3 py-1 rounded text-xs font-semibold transition-colors",
            saved
              ? "bg-emerald-900/50 text-emerald-400 border border-emerald-800"
              : hasChanges
                ? "bg-yellow-600/20 text-yellow-400 border border-yellow-700/50 hover:bg-yellow-600/30"
                : "bg-gray-800 text-gray-700 border border-gray-800 cursor-default"
          )}
        >
          {saving ? "Saving…" : saved ? "✓ Saved" : "Save changes"}
        </button>
        {error && <span className="text-[11px] text-red-500">{error}</span>}
      </div>
    </div>
  );
}


// ── Main card ─────────────────────────────────────────────────────────────────


export const SignalCard = memo(function SignalCard({ signal, isNew, livePrice, onUpdate }: {
  signal: Signal;
  isNew?: boolean;
  livePrice?: number | null;
  onUpdate?: (updates: Partial<Signal>) => void;
}) {
  const isLong     = signal.direction === "LONG";
  const isOpen     = !signal.result;
  const isWin      = signal.result === "win";
  const isRiding   = isOpen && !!signal.tp1_hit;
  const age        = fmtAge(signal.created_at);
  const leverage   = signal.leverage ?? 1;
  const [expanded, setExpanded] = useState(false);
  const t = tier(signal.confidence);

  // ── P&L calculations ───────────────────────────────────────────────────────
  const tp1Gain = isLong
    ? (signal.tp1 - signal.entry_price) / signal.entry_price * 100
    : (signal.entry_price - signal.tp1) / signal.entry_price * 100;

  const tp2Gain = signal.tp2 > 0
    ? (isLong
        ? (signal.tp2 - signal.entry_price) / signal.entry_price * 100
        : (signal.entry_price - signal.tp2) / signal.entry_price * 100)
    : null;

  // When riding to TP2, SL is at breakeven — use that for the loss reference
  const activeSl   = isRiding && signal.breakeven_sl ? signal.breakeven_sl : signal.sl;
  const slLoss     = Math.abs(activeSl - signal.entry_price) / signal.entry_price * 100;

  // For the PnL bar: when riding, target is TP2; otherwise TP1
  const pnlTarget  = isRiding && signal.tp2 > 0 ? signal.tp2 : signal.tp1;
  const pnlTargetGain = isLong
    ? (pnlTarget - signal.entry_price) / signal.entry_price * 100
    : (signal.entry_price - pnlTarget) / signal.entry_price * 100;

  const resolvedPrice = livePrice ?? null;
  const livePnl = isOpen && resolvedPrice !== null
    ? (isLong
        ? (resolvedPrice - signal.entry_price)
        : (signal.entry_price - resolvedPrice)) / signal.entry_price * 100
    : null;

  // ── Accent border ──────────────────────────────────────────────────────────
  const borderColor = !isOpen
    ? "border-l-gray-700"
    : isRiding
      ? "border-l-yellow-500"           // golden — locked-in profit ride
      : livePnl === null
        ? (isLong ? "border-l-emerald-700" : "border-l-red-700")
        : livePnl >= 0 ? "border-l-emerald-500" : "border-l-red-600";

  const reasons = (signal.triggers ?? [])
    .map(tr => plainReason(tr.label))
    .filter(Boolean)
    .slice(0, 4);

  return (
    <div className={clsx(
      "bg-gray-900 border border-gray-800 border-l-2 rounded-lg overflow-hidden transition-shadow",
      borderColor,
      !isOpen && "opacity-65",
      isNew && "ring-1 ring-emerald-800/40 shadow-[0_0_12px_rgba(52,211,153,0.06)]",
    )}>

      {/* ── TP1 hit banner ── */}
      {isRiding && (
        <div className="px-4 py-1 text-[11px] font-bold tracking-wide flex items-center justify-between bg-yellow-950/40 text-yellow-500 border-b border-yellow-900/30">
          <span>🎯 TP1 Hit — Riding to TP2</span>
          <span className="font-normal text-yellow-700 text-[10px]">SL → breakeven · risk-free</span>
        </div>
      )}

      {/* ── Resolved banner (History page only) ── */}
      {!isOpen && (
        <div className={clsx(
          "px-4 py-1 text-[11px] font-bold tracking-wide flex items-center justify-between",
          isWin ? "bg-emerald-950/50 text-emerald-600" : "bg-red-950/50 text-red-700"
        )}>
          <span>{isWin ? "WIN" : "STOPPED OUT"}</span>
          {signal.result_at && (
            <span className="font-normal text-gray-700">{fmtDuration(signal.created_at, signal.result_at)}</span>
          )}
        </div>
      )}

      {/* ── Header ── */}
      <div className="px-4 pt-3.5 pb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="font-mono font-bold text-white text-sm tracking-wide leading-none">
              {signal.symbol}
            </span>
            <span className={clsx(
              "text-xs font-bold px-1.5 py-0.5 rounded",
              isLong
                ? "bg-emerald-950 text-emerald-400"
                : "bg-red-950 text-red-400"
            )}>
              {signal.direction}
            </span>
            <span className="text-[11px] text-gray-600">{signal.timeframe}</span>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="flex items-center gap-1.5 justify-end">
            <span className={clsx("text-xs font-bold", t.color)}>{t.label}</span>
            <span className="text-xs text-gray-500 tabular-nums">{signal.confidence.toFixed(0)}%</span>
          </div>
          <div className="text-[11px] text-gray-700 mt-0.5 tabular-nums">{age}</div>
        </div>
      </div>

      {/* ── Live position box (open only) ── */}
      {isOpen && (
        <div className="mx-4 mb-3">
          {livePrice == null ? (
            <div className="flex items-center gap-2 bg-gray-800/30 border border-gray-800 rounded-lg px-3 py-2.5">
              <span className="w-1.5 h-1.5 rounded-full bg-gray-700 shrink-0" />
              <span className="text-xs text-gray-700">Fetching live price…</span>
            </div>
          ) : (
            <div className={clsx(
              "rounded-lg px-3 pt-2.5 pb-2 border",
              livePnl !== null && livePnl >= 0
                ? "bg-emerald-950/40 border-emerald-900/50"
                : "bg-red-950/40 border-red-900/50"
            )}>
              {/* Price + P&L row */}
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className={clsx(
                      "w-1.5 h-1.5 rounded-full shrink-0",
                      livePnl !== null && livePnl >= 0 ? "bg-emerald-400" : "bg-red-400"
                    )} />
                    <span className="text-[10px] text-gray-600 font-medium">LIVE</span>
                  </div>
                  <div className="font-mono font-bold text-white text-lg tabular-nums leading-none">
                    ${resolvedPrice !== null ? fmt(resolvedPrice) : ""}
                  </div>
                </div>

                {livePnl !== null && (
                  <div className="text-right">
                    <div className={clsx(
                      "font-bold tabular-nums text-xl leading-none",
                      livePnl >= 0 ? "text-emerald-400" : "text-red-400"
                    )}>
                      {livePnl >= 0 ? "+" : ""}{livePnl.toFixed(2)}%
                    </div>
                    {leverage > 1 && (
                      <div className={clsx(
                        "text-xs tabular-nums mt-0.5",
                        livePnl >= 0 ? "text-emerald-700" : "text-red-800"
                      )}>
                        {livePnl * leverage >= 0 ? "+" : ""}{(livePnl * leverage).toFixed(1)}% lev
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Progress bar */}
              {livePnl !== null && (
                <PnlBar pnl={livePnl} tp1Gain={pnlTargetGain} slLoss={slLoss} />
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Price table ── */}
      <div className="px-4 pb-3 space-y-1.5 text-xs">
        {/* Entry zone */}
        <div className="flex items-center gap-2">
          <span className="text-gray-600 w-10 shrink-0">Entry</span>
          <span className="font-mono text-gray-500 tabular-nums">
            {fmt(signal.entry_low)} – {fmt(signal.entry_high)}
          </span>
        </div>
        {/* TP1 — show as struck-through / done when riding */}
        <div className="flex items-center gap-2">
          <span className={clsx("w-10 shrink-0", isRiding ? "text-yellow-700" : "text-gray-600")}>TP1</span>
          <span className={clsx("font-mono tabular-nums font-medium", isRiding ? "text-yellow-600 line-through" : "text-gray-200")}>
            {fmt(signal.tp1)}
          </span>
          {isRiding ? (
            <span className="ml-auto text-yellow-700 text-[10px]">✓ hit</span>
          ) : (
            <div className="ml-auto flex items-center gap-2.5">
              <span className="text-emerald-500 tabular-nums">+{tp1Gain.toFixed(2)}%</span>
              {leverage > 1 && (
                <span className="text-emerald-800 tabular-nums">+{(tp1Gain * leverage).toFixed(1)}% lev</span>
              )}
            </div>
          )}
        </div>
        {/* TP2 — highlight as active target when riding */}
        {tp2Gain !== null && (
          <div className="flex items-center gap-2">
            <span className={clsx("w-10 shrink-0", isRiding ? "text-emerald-500 font-bold" : "text-gray-600")}>TP2</span>
            <span className={clsx("font-mono tabular-nums", isRiding ? "text-emerald-300 font-bold" : "text-gray-500")}>
              {fmt(signal.tp2)}
            </span>
            <div className="ml-auto flex items-center gap-2.5">
              <span className={clsx("tabular-nums", isRiding ? "text-emerald-400 font-semibold" : "text-emerald-600/70")}>
                +{tp2Gain.toFixed(2)}%
              </span>
              {leverage > 1 && (
                <span className={clsx("tabular-nums", isRiding ? "text-emerald-700" : "text-emerald-900")}>
                  +{(tp2Gain * leverage).toFixed(1)}% lev
                </span>
              )}
            </div>
          </div>
        )}
        {/* SL — show breakeven when riding */}
        <div className="flex items-center gap-2">
          <span className={clsx("w-10 shrink-0", isRiding ? "text-yellow-700" : "text-gray-600")}>SL</span>
          <span className={clsx("font-mono tabular-nums", isRiding ? "text-yellow-700" : "text-gray-600")}>
            {fmt(activeSl)}
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            {isRiding ? (
              <span className="text-yellow-700 text-[10px]">breakeven</span>
            ) : (
              <span className="text-red-600 tabular-nums">−{slLoss.toFixed(2)}%</span>
            )}
          </div>
        </div>
      </div>

      {/* ── Footer ── */}
      <div className="border-t border-gray-800/50 px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-3 text-xs text-gray-600">
          <span>
            R/R <span className="text-gray-400 font-medium">{signal.risk_reward}</span>
          </span>
          {leverage > 1 && (
            <>
              <span className="text-gray-800">·</span>
              <span>{leverage}× isolated</span>
            </>
          )}
        </div>
        <button
          onClick={() => setExpanded(v => !v)}
          className="text-gray-700 hover:text-gray-400 transition-colors text-sm px-1.5 py-0.5 leading-none"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? "▴" : "▾"}
        </button>
      </div>

      {/* ── Expand drawer ── */}
      {expanded && (
        <div className="border-t border-gray-800/50 px-4 py-3 space-y-3">
          {/* Signal reasons */}
          {reasons.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-[10px] text-gray-700 font-semibold uppercase tracking-wider">Why</p>
              {reasons.map((r, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-gray-500">
                  <span className="text-gray-700 mt-px shrink-0">·</span>
                  <span>{r}</span>
                </div>
              ))}
            </div>
          )}

          {/* Market context */}
          {(signal.rsi != null || signal.volume_ratio != null || signal.funding_rate != null) && (
            <div className="flex items-center gap-3 text-xs text-gray-700 flex-wrap">
              {signal.rsi != null && (
                <span>RSI <span className={clsx(
                  signal.rsi > 70 ? "text-red-500" : signal.rsi < 30 ? "text-emerald-500" : "text-gray-400"
                )}>{signal.rsi.toFixed(0)}</span></span>
              )}
              {signal.volume_ratio != null && (
                <span>Vol <span className={clsx(
                  signal.volume_ratio > 2 ? "text-yellow-500" : "text-gray-400"
                )}>{signal.volume_ratio.toFixed(1)}×</span></span>
              )}
              {signal.funding_rate != null && (
                <span>Funding <span className={clsx(
                  Math.abs(signal.funding_rate) > 0.05 ? "text-yellow-600" : "text-gray-400"
                )}>{signal.funding_rate > 0 ? "+" : ""}{signal.funding_rate.toFixed(4)}%</span></span>
              )}
            </div>
          )}

          {/* Copy buttons */}
          <div className="flex items-center gap-2.5 text-[11px] text-gray-700 flex-wrap">
            <CopyBtn label="copy entry" value={String(signal.entry_price)} />
            <span className="text-gray-800">·</span>
            <CopyBtn label="copy TP1" value={String(signal.tp1)} />
            {signal.tp2 > 0 && (
              <>
                <span className="text-gray-800">·</span>
                <CopyBtn label="copy TP2" value={String(signal.tp2)} />
              </>
            )}
            <span className="text-gray-800">·</span>
            <CopyBtn label="copy SL" value={String(signal.sl)} />
          </div>

          {/* Manual override — open signals only */}
          {isOpen && onUpdate && (
            <>
              <div className="border-t border-gray-800/50 pt-3" />
              <ManualOverride signal={signal} onUpdate={onUpdate} />
            </>
          )}
        </div>
      )}
    </div>
  );
});
