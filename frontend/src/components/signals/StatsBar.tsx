"use client";
import clsx from "clsx";

interface StatsBarProps {
  stats: {
    total: number;
    wins: number;
    losses: number;
    breakevens?: number;
    open: number;
    riding?: number;
    win_rate: number;
    avg_confidence: number;
  } | null;
  loading?: boolean;
}

export function StatsBar({ stats, loading }: StatsBarProps) {
  if (loading) return (
    <div className="h-3.5 w-40 bg-gray-800 rounded animate-pulse" />
  );
  if (!stats) return null;

  const decided = stats.wins + stats.losses;
  const wrColor =
    decided === 0 ? "text-gray-600" :
    stats.win_rate >= 60 ? "text-emerald-400" :
    stats.win_rate >= 45 ? "text-yellow-400" :
    "text-red-400";

  return (
    <div className="flex items-center gap-2 text-xs text-gray-600 flex-wrap">
      {decided > 0 ? (
        <span>
          <span className={clsx("font-semibold", wrColor)}>{stats.win_rate}%</span>
          <span className="ml-1 text-gray-700">
            {stats.wins}W · {stats.losses}L
            {(stats.breakevens ?? 0) > 0 && ` · ${stats.breakevens}BE`}
          </span>
        </span>
      ) : (
        <span className="text-gray-700">no results yet</span>
      )}
      <span className="text-gray-800">·</span>
      <span>
        <span className={clsx("font-semibold", stats.open > 0 ? "text-sky-400" : "text-gray-500")}>
          {stats.open}
        </span>
        <span className="ml-1">open</span>
      </span>
      {(stats.riding ?? 0) > 0 && (
        <>
          <span className="text-gray-800">·</span>
          <span>
            <span className="font-semibold text-yellow-500">{stats.riding}</span>
            <span className="ml-1 text-yellow-700">riding</span>
          </span>
        </>
      )}
      <span className="text-gray-800">·</span>
      <span>{stats.total} total</span>
    </div>
  );
}
