"use client";
import clsx from "clsx";

interface StatsBarProps {
  stats: {
    total: number;
    wins: number;
    losses: number;
    win_rate: number;
    longs: number;
    shorts: number;
    avg_confidence: number;
  } | null;
}

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card flex-1 min-w-[110px]">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="text-xl font-bold text-white">{value}</div>
      {sub && <div className="text-xs text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}

export function StatsBar({ stats }: StatsBarProps) {
  if (!stats) return null;
  return (
    <div className="flex flex-wrap gap-3">
      <Stat label="Total signals" value={stats.total} />
      <Stat
        label="Win rate"
        value={`${stats.win_rate}%`}
        sub={`${stats.wins}W / ${stats.losses}L`}
      />
      <Stat label="Avg confidence" value={`${stats.avg_confidence}%`} />
      <Stat
        label="Direction split"
        value={`${stats.longs}L / ${stats.shorts}S`}
        sub="long / short"
      />
    </div>
  );
}
