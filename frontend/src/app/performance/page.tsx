"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import clsx from "clsx";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer,
} from "recharts";

// ── Types ─────────────────────────────────────────────────────────────────────

interface PerfRow {
  symbol: string;
  timeframe: string;
  wins: number;
  losses: number;
  expired: number;
  total: number;
  win_rate: number | null;
  avg_duration_hours: number | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const BACKTEST_WR = 63;

function wrColor(wr: number | null) {
  if (wr == null) return "text-gray-600";
  if (wr >= 70) return "text-emerald-400";
  if (wr >= 55) return "text-yellow-400";
  return "text-red-400";
}

function wrBar(wr: number | null) {
  if (wr == null) return null;
  const pct = Math.min(100, wr);
  const color = wr >= 70 ? "bg-emerald-500" : wr >= 55 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="w-full h-1.5 bg-gray-700 rounded-full overflow-hidden">
      <div className={clsx("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

function adjLabel(wr: number | null): string {
  if (wr == null) return "—";
  const adj = Math.max(-10, Math.min(10, (wr - 60) * 0.5));
  return adj >= 0 ? `+${adj.toFixed(1)} pts` : `${adj.toFixed(1)} pts`;
}

function adjColor(wr: number | null): string {
  if (wr == null) return "text-gray-600";
  const adj = (wr - 60) * 0.5;
  if (adj > 1) return "text-emerald-400";
  if (adj < -1) return "text-red-400";
  return "text-gray-400";
}

// ── Summary cards ─────────────────────────────────────────────────────────────

function Summary({ rows }: { rows: PerfRow[] }) {
  const decided = rows.reduce((a, r) => a + r.wins + r.losses, 0);
  const wins = rows.reduce((a, r) => a + r.wins, 0);
  const liveWR = decided > 0 ? Math.round((wins / decided) * 100) : null;
  const best = rows
    .filter((r) => r.win_rate != null && r.wins + r.losses >= 5)
    .sort((a, b) => (b.win_rate ?? 0) - (a.win_rate ?? 0))[0];
  const bestSymbol = best ? `${best.symbol}/${best.timeframe}` : "—";

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {[
        { label: "Total resolved", value: decided, sub: `${wins}W / ${decided - wins}L` },
        {
          label: "Live win rate",
          value: liveWR != null ? `${liveWR}%` : "—",
          sub: `vs ${BACKTEST_WR}% backtest`,
          highlight: liveWR != null && liveWR >= BACKTEST_WR,
        },
        { label: "Symbols tracked", value: rows.length, sub: "symbol / timeframe pairs" },
        {
          label: "Best performer",
          value: bestSymbol,
          sub: best
            ? `${best.win_rate?.toFixed(0)}% WR (${best.wins + best.losses} trades)`
            : "need ≥5 trades",
        },
      ].map((s) => (
        <div key={s.label} className="card">
          <div className="text-xs text-gray-500 mb-1">{s.label}</div>
          <div className={clsx("text-xl font-bold", (s as any).highlight ? "text-emerald-400" : "text-white")}>
            {s.value}
          </div>
          {s.sub && <div className="text-xs text-gray-600 mt-0.5">{s.sub}</div>}
        </div>
      ))}
    </div>
  );
}

// ── Equity curve chart ────────────────────────────────────────────────────────

function EquityCurveChart({ curve, startingBalance }: { curve: any[]; startingBalance: number }) {
  const chartData = [
    { label: "Start", balance: startingBalance, pnl: 0 },
    ...curve.map((c) => ({
      label: c.date.slice(0, 10),
      balance: c.balance,
      pnl: c.pnl,
      result: c.result,
      symbol: c.symbol,
    })),
  ];

  const final = chartData[chartData.length - 1]?.balance ?? startingBalance;
  const isUp = final >= startingBalance;
  const strokeColor = isUp ? "#10b981" : "#ef4444";
  const gradId = isUp ? "gradUp" : "gradDown";

  const CustomTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null;
    const d = payload[0].payload;
    return (
      <div className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-xs space-y-0.5">
        <div className="text-gray-400">{d.label}</div>
        <div className="text-white font-bold">${d.balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
        {d.pnl !== 0 && (
          <div className={d.pnl >= 0 ? "text-emerald-400" : "text-red-400"}>
            {d.pnl >= 0 ? "+" : ""}${d.pnl.toFixed(2)}
          </div>
        )}
        {d.symbol && (
          <div className="text-gray-500">{d.symbol} · {d.result?.toUpperCase()}</div>
        )}
      </div>
    );
  };

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="gradUp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#10b981" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#10b981" stopOpacity={0.02} />
          </linearGradient>
          <linearGradient id="gradDown" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#ef4444" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
        <XAxis dataKey="label" tick={{ fill: "#6b7280", fontSize: 10 }} tickLine={false} axisLine={false} />
        <YAxis
          tick={{ fill: "#6b7280", fontSize: 10 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
          width={46}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={startingBalance} stroke="#4b5563" strokeDasharray="4 4" strokeWidth={1} />
        <Area
          type="monotone"
          dataKey="balance"
          stroke={strokeColor}
          strokeWidth={2}
          fill={`url(#${gradId})`}
          dot={false}
          activeDot={{ r: 4, fill: strokeColor }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Portfolio panel ───────────────────────────────────────────────────────────

function PortfolioPanel() {
  const [data, setData] = useState<{ summary: any; curve: any[] } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.equityCurve()
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="card py-8 text-center text-gray-500 text-sm animate-pulse">Loading portfolio...</div>;

  if (!data || data.curve.length === 0) {
    return (
      <div className="card space-y-3">
        <div>
          <h2 className="font-semibold text-white">Paper Portfolio</h2>
          <p className="text-xs text-gray-500 mt-0.5">$10,000 virtual — every fired signal is auto-tracked as a paper trade</p>
        </div>
        <div className="py-8 text-center text-gray-600 text-sm">
          Portfolio will appear once the first signals resolve (win or loss).
          <div className="text-xs text-gray-700 mt-1">The system is already tracking all open signals.</div>
        </div>
      </div>
    );
  }

  const { summary, curve } = data;
  const isPositive = summary.total_return_pct >= 0;

  const metrics = [
    {
      label: "Final balance",
      value: `$${summary.final_balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}`,
      color: isPositive ? "text-emerald-400" : "text-red-400",
    },
    {
      label: "Max drawdown",
      value: `${summary.max_drawdown_pct.toFixed(1)}%`,
      color: summary.max_drawdown_pct > 15 ? "text-red-400" : "text-yellow-400",
    },
    {
      label: "Profit factor",
      value: summary.profit_factor != null ? summary.profit_factor.toFixed(2) : "—",
      color: summary.profit_factor != null && summary.profit_factor >= 1.5 ? "text-emerald-400" : "text-gray-300",
      sub: "gross win / gross loss",
    },
    {
      label: "Sharpe ratio",
      value: summary.sharpe_ratio != null ? summary.sharpe_ratio.toFixed(2) : "—",
      color: summary.sharpe_ratio != null && summary.sharpe_ratio > 0.5 ? "text-emerald-400" : "text-gray-300",
      sub: summary.total_trades < 20 ? "need 20+ trades" : "per-trade",
    },
  ];

  return (
    <div className="card space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="font-semibold text-white">Paper Portfolio</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            $10,000 virtual — every signal auto-tracked from entry price to TP1 / SL
          </p>
        </div>
        <div className="text-right">
          <div className={clsx("text-2xl font-bold", isPositive ? "text-emerald-400" : "text-red-400")}>
            {isPositive ? "+" : ""}{summary.total_return_pct.toFixed(1)}%
          </div>
          <div className="text-xs text-gray-600">{summary.total_trades} trades</div>
        </div>
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {metrics.map((m) => (
          <div key={m.label} className="bg-gray-800/50 rounded-lg p-2.5">
            <div className="text-[10px] text-gray-500">{m.label}</div>
            <div className={clsx("text-lg font-bold mt-0.5", m.color)}>{m.value}</div>
            {m.sub && <div className="text-[9px] text-gray-700 mt-0.5">{m.sub}</div>}
          </div>
        ))}
      </div>

      {/* Current streak */}
      {summary.current_streak > 0 && (
        <div className={clsx(
          "text-xs px-3 py-2 rounded border flex items-center gap-2",
          summary.current_streak_type === "win"
            ? "bg-emerald-900/20 border-emerald-700/30 text-emerald-400"
            : "bg-red-900/20 border-red-700/30 text-red-400"
        )}>
          <span>{summary.current_streak_type === "win" ? "🔥" : "⚠"}</span>
          <span>
            Current streak: <strong>{summary.current_streak} consecutive {summary.current_streak_type}s</strong>
            {summary.current_streak_type === "loss" && summary.current_streak >= 3
              ? " — circuit breaker active, new signals paused"
              : ""}
          </span>
        </div>
      )}

      {/* Equity curve chart */}
      <EquityCurveChart curve={curve} startingBalance={10000} />

      {/* Disclaimer */}
      <p className="text-[10px] text-gray-600">
        Simulated paper trade: entry at signal price, WIN exits at TP1, LOSS exits at SL.
        Real execution may differ due to slippage and missed fills. Not financial advice.
      </p>
    </div>
  );
}

// ── ML Dataset panel ──────────────────────────────────────────────────────────

function MLDatasetPanel() {
  const [stats, setStats] = useState<any>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    api.mlStats().then(setStats).catch(console.error);
  }, []);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const data = await api.mlExport();
      const json = JSON.stringify(data, null, 2);
      const blob = new Blob([json], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `mhoo_ml_dataset_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error(e);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="card space-y-4">
      <div>
        <h2 className="font-semibold text-white">ML Training Dataset</h2>
        <p className="text-xs text-gray-500 mt-0.5">
          Feature snapshots captured at signal creation — 30+ indicators per row
        </p>
      </div>

      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: "Feature rows", value: stats.total_feature_rows, sub: "signals tracked" },
            { label: "Labelled", value: stats.labelled_rows, sub: "with outcome", highlight: true },
            { label: "Wins recorded", value: stats.labelled_wins, color: "text-emerald-400" },
            { label: "Losses recorded", value: stats.labelled_losses, color: "text-red-400" },
          ].map((s) => (
            <div key={s.label} className="bg-gray-800/50 rounded-lg p-3">
              <div className="text-[11px] text-gray-500">{s.label}</div>
              <div className={clsx("text-xl font-bold mt-0.5", (s as any).color || "text-white")}>
                {s.value ?? "—"}
              </div>
              {s.sub && <div className="text-[10px] text-gray-600 mt-0.5">{s.sub}</div>}
            </div>
          ))}
        </div>
      )}

      <div className="rounded bg-gray-800/40 p-3 text-xs text-gray-400 space-y-1">
        <div className="font-medium text-gray-300">Features captured per signal:</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-gray-500">
          {[
            "Price returns (1h / 4h / 24h)",
            "Regime (bull/bear/sideways)",
            "EMA gaps (9 / 21 / 50 / 200)",
            "RSI-14 + slope",
            "MACD line / signal / hist / slope",
            "Bollinger Band position + width",
            "ATR %",
            "ADX + ±DI",
            "Volume ratio + 3-bar trend",
            "Candle body / upper / lower shadow",
            "Fear & Greed index",
            "Funding rate",
            "Open Interest change",
            "Hour UTC + day of week",
            "MFE / MAE (filled on resolution)",
            "Actual PnL% + hold duration",
          ].map((f) => (
            <div key={f} className="flex items-center gap-1">
              <span className="text-emerald-600">•</span> {f}
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={handleDownload}
          disabled={downloading || !stats || stats.labelled_rows === 0}
          className="btn-primary disabled:opacity-40 text-sm"
        >
          {downloading ? "Downloading..." : "Download JSON dataset"}
        </button>
        {stats && stats.labelled_rows === 0 && (
          <span className="text-xs text-gray-500">No labelled data yet — needs resolved signals</span>
        )}
        {stats && stats.labelled_rows > 0 && stats.labelled_rows < 50 && (
          <span className="text-xs text-yellow-600">
            {stats.labelled_rows} rows — ML models typically need 200+ for reliable training
          </span>
        )}
        {stats && stats.labelled_rows >= 50 && (
          <span className="text-xs text-emerald-600">
            {stats.labelled_rows} rows ready for training
          </span>
        )}
      </div>

      <p className="text-[10px] text-gray-600">
        Load in Python:{" "}
        <span className="font-mono bg-gray-800 px-1 rounded">
          df = pd.read_json(&apos;dataset.json&apos;)
        </span>
        {" "}→ train any classifier on{" "}
        <span className="font-mono bg-gray-800 px-1 rounded">label</span> column
      </p>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

// ── Grade calibration panel ───────────────────────────────────────────────────

interface CalRow {
  tier: string;
  total: number;
  wins: number;
  losses: number;
  live_wr: number | null;
  expected_wr: number;
}

const CAL_COLOR: Record<string, string> = {
  ALPHA: "text-emerald-400",
  PRIME: "text-yellow-400",
  SETUP: "text-orange-400",
};

function GradeCalibration() {
  const [rows, setRows] = useState<CalRow[]>([]);

  useEffect(() => {
    api.calibration().then((data: any[]) => setRows(data)).catch(console.error);
  }, []);

  const hasData = rows.some(r => r.wins + r.losses > 0);
  if (!hasData) return null;

  return (
    <div className="card space-y-4">
      <div>
        <h2 className="font-semibold text-white text-sm">Grade Calibration</h2>
        <p className="text-xs text-gray-500 mt-0.5">Live win rate per confidence tier vs. backtest expectation</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {rows.map(r => {
          const decided = r.wins + r.losses;
          const delta = r.live_wr != null ? r.live_wr - r.expected_wr : null;
          const deltaColor = delta == null ? "text-gray-600" : delta >= 5 ? "text-emerald-400" : delta >= -5 ? "text-yellow-400" : "text-red-400";
          const barColor = r.live_wr == null ? "" : r.live_wr >= 60 ? "bg-emerald-600" : r.live_wr >= 45 ? "bg-yellow-500" : "bg-red-600";

          return (
            <div key={r.tier} className="bg-gray-800/40 rounded-lg p-3 space-y-2.5">
              {/* Tier label + live WR */}
              <div className="flex items-baseline justify-between">
                <span className={clsx("text-sm font-bold", CAL_COLOR[r.tier])}>{r.tier}</span>
                <span className={clsx("text-xl font-bold tabular-nums", wrColor(r.live_wr))}>
                  {r.live_wr != null ? `${r.live_wr}%` : "—"}
                </span>
              </div>

              {/* Bar: live vs expected */}
              <div className="space-y-1">
                <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
                  {r.live_wr != null && (
                    <div className={clsx("h-full rounded-full", barColor)} style={{ width: `${r.live_wr}%` }} />
                  )}
                </div>
                <div className="h-1 bg-gray-700/50 rounded-full overflow-hidden">
                  <div className="h-full bg-gray-600 rounded-full" style={{ width: `${r.expected_wr}%` }} />
                </div>
              </div>

              {/* Stats row */}
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-600">
                  <span className="text-emerald-700">{r.wins}W</span>·<span className="text-red-800">{r.losses}L</span>
                  {r.total - decided > 0 && <span className="text-gray-700">·{r.total - decided} open</span>}
                </span>
                <span className={clsx("font-medium tabular-nums", deltaColor)}>
                  {delta != null ? (delta >= 0 ? `+${delta.toFixed(1)}` : delta.toFixed(1)) : "—"} vs target
                </span>
              </div>

              <div className="text-[10px] text-gray-700">
                Target {r.expected_wr}% · darker bar
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


export default function PerformancePage() {
  const [rows, setRows] = useState<PerfRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<"symbol" | "wr" | "total">("wr");

  useEffect(() => {
    api
      .performance()
      .then(setRows)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const sorted = [...rows].sort((a, b) => {
    if (sort === "wr") return (b.win_rate ?? -1) - (a.win_rate ?? -1);
    if (sort === "total") return b.total - a.total;
    return `${a.symbol}/${a.timeframe}`.localeCompare(`${b.symbol}/${b.timeframe}`);
  });

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-bold text-white">Performance</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Every signal auto-tracked as a paper trade — live win rates feed back into confidence scoring
        </p>
      </div>

      {/* Paper portfolio — always visible */}
      <PortfolioPanel />

      {/* Grade calibration — always visible once data exists */}
      <GradeCalibration />

      {loading ? (
        <div className="text-gray-500 text-sm animate-pulse">Loading...</div>
      ) : rows.length === 0 ? (
        <div className="card text-center py-16 text-gray-600 text-sm">
          No resolved signals yet. Win/Loss data will appear here as signals close.
        </div>
      ) : (
        <>
          <Summary rows={rows} />

          {/* How it works */}
          <div className="rounded-lg bg-blue-900/20 border border-blue-500/30 px-4 py-3 text-xs text-blue-300 space-y-1">
            <div className="font-semibold text-blue-200">How performance feeds back into predictions</div>
            <div>
              Each scan, the scorer reads the live win rate for that symbol/timeframe and adjusts
              confidence by up to ±10 points (requires ≥10 resolved trades). A symbol beating
              the 60% baseline gets a confidence boost — an underperformer gets a penalty.
            </div>
            <div className="text-blue-400 mt-1">
              Formula: adjustment = (live WR − 60%) × 0.5 pts/pp, capped at ±10 pts
            </div>
          </div>

          {/* Table */}
          <div className="card overflow-x-auto">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold text-white text-sm">Breakdown by symbol</h2>
              <div className="flex items-center gap-1 text-xs">
                <span className="text-gray-500 mr-1">Sort:</span>
                {(["wr", "total", "symbol"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => setSort(s)}
                    className={clsx(
                      "px-2 py-0.5 rounded transition-colors",
                      sort === s ? "bg-gray-700 text-white" : "text-gray-500 hover:text-gray-200"
                    )}
                  >
                    {s === "wr" ? "Win rate" : s === "total" ? "Volume" : "Symbol"}
                  </button>
                ))}
              </div>
            </div>

            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800 text-left">
                  <th className="pb-2 font-medium">Symbol</th>
                  <th className="pb-2 font-medium text-right">W/L</th>
                  <th className="pb-2 font-medium text-right">Exp</th>
                  <th className="pb-2 font-medium w-28">Win rate</th>
                  <th className="pb-2 font-medium text-right">Conf adj</th>
                  <th className="pb-2 font-medium text-right">Avg hold</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => {
                  const decided = r.wins + r.losses;
                  return (
                    <tr
                      key={`${r.symbol}/${r.timeframe}`}
                      className="border-b border-gray-800/50 hover:bg-gray-800/20 transition-colors"
                    >
                      <td className="py-2">
                        <div className="font-semibold text-white">{r.symbol}</div>
                        <div className="text-gray-600">{r.timeframe}</div>
                      </td>
                      <td className="py-2 text-right">
                        <span className="text-emerald-400">{r.wins}</span>
                        <span className="text-gray-600">/</span>
                        <span className="text-red-400">{r.losses}</span>
                      </td>
                      <td className="py-2 text-right text-gray-600">{r.expired}</td>
                      <td className="py-2 w-28">
                        <div className="flex items-center gap-2">
                          <span className={clsx("font-bold w-10 text-right", wrColor(r.win_rate))}>
                            {r.win_rate != null ? `${r.win_rate}%` : "—"}
                          </span>
                          <div className="flex-1">{wrBar(r.win_rate)}</div>
                        </div>
                        {decided < 10 && (
                          <div className="text-[10px] text-gray-600 mt-0.5">
                            {decided}/10 trades for adj
                          </div>
                        )}
                      </td>
                      <td className={clsx("py-2 text-right font-mono font-bold", adjColor(r.win_rate))}>
                        {adjLabel(r.win_rate)}
                      </td>
                      <td className="py-2 text-right text-gray-400">
                        {r.avg_duration_hours != null
                          ? r.avg_duration_hours < 1
                            ? `${Math.round(r.avg_duration_hours * 60)}m`
                            : `${r.avg_duration_hours}h`
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="text-[11px] text-gray-600">
            Outcome tracker checks every 60 seconds using OHLC candles — SL-first detection per bar.
            Confidence adjustments applied automatically on the next scan cycle.
          </p>
        </>
      )}

      {/* ML Dataset — always visible */}
      <MLDatasetPanel />
    </div>
  );
}
