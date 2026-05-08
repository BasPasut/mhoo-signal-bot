"use client";
import { Signal } from "@/hooks/useWebSocket";
import clsx from "clsx";
import { format } from "date-fns";

function ConfidenceBar({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={clsx(
            "h-full rounded-full transition-all",
            value >= 80 ? "bg-emerald-400" : value >= 70 ? "bg-yellow-400" : "bg-orange-400"
          )}
          style={{ width: `${value}%` }}
        />
      </div>
      <span className="text-xs font-semibold text-gray-300 w-10 text-right">
        {value.toFixed(0)}%
      </span>
    </div>
  );
}

function PriceRow({
  label,
  price,
  base,
  color,
}: {
  label: string;
  price: number;
  base: number;
  color: string;
}) {
  const pct = ((price - base) / base) * 100;
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-gray-500">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-mono text-gray-200">${price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</span>
        <span className={clsx("text-xs font-medium", color)}>
          {pct >= 0 ? "+" : ""}{pct.toFixed(2)}%
        </span>
      </div>
    </div>
  );
}

export function SignalCard({ signal, isNew }: { signal: Signal; isNew?: boolean }) {
  const isLong = signal.direction === "LONG";

  return (
    <div
      className={clsx(
        "card border-l-4 transition-all",
        isLong ? "border-l-emerald-500" : "border-l-orange-500",
        isNew && "ring-1 ring-emerald-500/30 animate-pulse-once"
      )}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={clsx("text-lg font-bold", isLong ? "badge-long" : "badge-short")}>
            {isLong ? "▲" : "▼"} {signal.direction}
          </span>
          <span className="font-bold text-white text-lg">{signal.symbol}/USDT</span>
          <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
            {signal.timeframe}
          </span>
        </div>
        <div className="text-right">
          <div className="text-xs text-gray-500">
            {format(new Date(signal.created_at), "MMM d, HH:mm")}
          </div>
          <div className="text-xs text-gray-600 capitalize">{signal.risk_profile}</div>
        </div>
      </div>

      {/* Confidence */}
      <div className="mb-3">
        <div className="label">Confidence</div>
        <ConfidenceBar value={signal.confidence} />
      </div>

      {/* Entry + TP/SL */}
      <div className="space-y-1.5 mb-3">
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">Entry zone</span>
          <span className="font-mono text-gray-200 text-xs">
            ${signal.entry_low.toLocaleString()} – ${signal.entry_high.toLocaleString()}
          </span>
        </div>
        <PriceRow label="TP1" price={signal.tp1} base={signal.entry_price} color={isLong ? "text-emerald-400" : "text-orange-400"} />
        <PriceRow label="TP2" price={signal.tp2} base={signal.entry_price} color={isLong ? "text-emerald-400" : "text-orange-400"} />
        <PriceRow label="Stop loss" price={signal.sl} base={signal.entry_price} color="text-red-400" />
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">Risk / Reward</span>
          <span className="font-semibold text-gray-200">1 : {signal.risk_reward}</span>
        </div>
      </div>

      {/* Score breakdown */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        {[
          { label: "TA", value: signal.ta_score },
          { label: "Pattern", value: signal.pattern_score },
          { label: "ML", value: signal.ml_score },
          { label: "Context", value: signal.context_score },
        ].map((s) => (
          <div key={s.label} className="bg-gray-800/60 rounded-lg p-2 text-center">
            <div className="text-xs text-gray-500">{s.label}</div>
            <div className="font-semibold text-sm text-gray-200">{s.value.toFixed(0)}</div>
          </div>
        ))}
      </div>

      {/* Triggers */}
      {signal.triggers && signal.triggers.length > 0 && (
        <div>
          <div className="label">Signals</div>
          <div className="space-y-0.5">
            {signal.triggers.slice(0, 5).map((t, i) => (
              <div key={i} className="flex items-center gap-1.5 text-xs text-gray-400">
                <span className={clsx(
                  "w-1.5 h-1.5 rounded-full flex-shrink-0",
                  t.dir === "long" ? "bg-emerald-500" : t.dir === "short" ? "bg-orange-500" : "bg-gray-500"
                )} />
                {t.label}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Meta */}
      <div className="flex flex-wrap gap-3 mt-3 pt-3 border-t border-gray-800 text-xs text-gray-500">
        {signal.rsi != null && <span>RSI <span className="text-gray-300">{signal.rsi.toFixed(1)}</span></span>}
        {signal.volume_ratio != null && <span>Vol <span className="text-gray-300">{signal.volume_ratio.toFixed(1)}x</span></span>}
        {signal.funding_rate != null && (
          <span>Funding <span className={signal.funding_rate > 0 ? "text-red-400" : "text-emerald-400"}>
            {signal.funding_rate > 0 ? "+" : ""}{signal.funding_rate.toFixed(4)}%
          </span></span>
        )}
        {signal.fear_greed != null && <span>F&G <span className="text-gray-300">{signal.fear_greed}</span></span>}
        {signal.discord_sent && <span className="text-emerald-600">✓ Discord</span>}
      </div>
    </div>
  );
}
