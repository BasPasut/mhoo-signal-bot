"use client";
import clsx from "clsx";

interface Bucket {
  wins: number;
  losses: number;
  open: number;
  win_rate: number | null;
}

interface SymbolBucket extends Bucket {
  symbol: string;
}

interface LabeledBucket extends Bucket {
  label: string;
}

export interface AnalyticsData {
  by_grade: LabeledBucket[];
  by_direction: LabeledBucket[];
  by_symbol: SymbolBucket[];
  avg_hold_hours: number | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function wrTextColor(wr: number | null) {
  if (wr == null) return "text-gray-700";
  if (wr >= 60) return "text-emerald-400";
  if (wr >= 45) return "text-yellow-400";
  return "text-red-400";
}

function wrBarColor(wr: number) {
  if (wr >= 60) return "bg-emerald-600";
  if (wr >= 45) return "bg-yellow-500";
  return "bg-red-600";
}

function fmtHold(h: number | null) {
  if (h == null) return "—";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 24) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function MiniBar({ wr, className }: { wr: number | null; className?: string }) {
  return (
    <div className={clsx("h-1 bg-gray-800 rounded-full overflow-hidden", className)}>
      {wr != null && (
        <div
          className={clsx("h-full rounded-full transition-all", wrBarColor(wr))}
          style={{ width: `${Math.min(100, wr)}%` }}
        />
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] text-gray-600 font-semibold uppercase tracking-wider mb-3">
      {children}
    </p>
  );
}

function WLBadge({ wins, losses }: { wins: number; losses: number }) {
  const decided = wins + losses;
  if (decided === 0) return <span className="text-gray-700 text-xs">no data</span>;
  return (
    <span className="text-xs text-gray-700 tabular-nums">
      <span className="text-emerald-700">{wins}W</span>·<span className="text-red-800">{losses}L</span>
    </span>
  );
}

// ── Grade column ──────────────────────────────────────────────────────────────

const GRADE_COLOR: Record<string, string> = {
  ALPHA: "text-emerald-400",
  PRIME: "text-yellow-400",
  SETUP: "text-orange-400",
};

const GRADE_RANGE: Record<string, string> = {
  ALPHA: "≥80%",
  PRIME: "60–79%",
  SETUP: "<60%",
};

function GradeSection({ rows }: { rows: LabeledBucket[] }) {
  return (
    <div>
      <SectionTitle>By grade</SectionTitle>
      <div className="space-y-3">
        {rows.map(g => (
          <div key={g.label}>
            <div className="flex items-center justify-between mb-1 gap-2">
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={clsx("text-xs font-bold shrink-0", GRADE_COLOR[g.label])}>
                  {g.label}
                </span>
                <span className="text-[10px] text-gray-700 shrink-0">{GRADE_RANGE[g.label]}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className={clsx("text-xs font-bold tabular-nums w-8 text-right", wrTextColor(g.win_rate))}>
                  {g.win_rate != null ? `${g.win_rate}%` : "—"}
                </span>
                <WLBadge wins={g.wins} losses={g.losses} />
              </div>
            </div>
            <MiniBar wr={g.win_rate} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Direction column ──────────────────────────────────────────────────────────

function DirectionSection({ rows, avgHold }: { rows: LabeledBucket[]; avgHold: number | null }) {
  return (
    <div>
      <SectionTitle>Direction</SectionTitle>
      <div className="space-y-3">
        {rows.map(d => (
          <div key={d.label}>
            <div className="flex items-center justify-between mb-1 gap-2">
              <span className={clsx(
                "text-xs font-bold",
                d.label === "LONG" ? "text-emerald-400" : "text-red-400"
              )}>
                {d.label}
              </span>
              <div className="flex items-center gap-2 shrink-0">
                <span className={clsx("text-xs font-bold tabular-nums w-8 text-right", wrTextColor(d.win_rate))}>
                  {d.win_rate != null ? `${d.win_rate}%` : "—"}
                </span>
                <WLBadge wins={d.wins} losses={d.losses} />
              </div>
            </div>
            <MiniBar wr={d.win_rate} />
          </div>
        ))}
      </div>

      <div className="mt-4 pt-3 border-t border-gray-800/60 flex items-center justify-between text-xs">
        <span className="text-gray-600">Avg hold time</span>
        <span className="text-gray-400 font-medium tabular-nums">{fmtHold(avgHold)}</span>
      </div>
    </div>
  );
}

// ── Symbol column ─────────────────────────────────────────────────────────────

function SymbolSection({ rows }: { rows: SymbolBucket[] }) {
  const display = rows.slice(0, 7);
  return (
    <div>
      <SectionTitle>By coin</SectionTitle>
      {display.length === 0 ? (
        <p className="text-xs text-gray-700">No signals yet</p>
      ) : (
        <div className="space-y-2">
          {display.map(s => {
            const name = s.symbol.replace("USDT", "");
            const decided = s.wins + s.losses;
            return (
              <div key={s.symbol} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-gray-300 w-14 shrink-0 truncate">{name}</span>
                <MiniBar wr={s.win_rate} className="flex-1" />
                <span className={clsx("font-bold tabular-nums w-8 text-right shrink-0", wrTextColor(s.win_rate))}>
                  {s.win_rate != null ? `${s.win_rate}%` : "—"}
                </span>
                <span className="text-gray-700 w-12 text-right shrink-0 tabular-nums">
                  {decided > 0
                    ? <><span className="text-emerald-700">{s.wins}W</span>·<span className="text-red-800">{s.losses}L</span></>
                    : s.open > 0 ? `${s.open} open` : "—"
                  }
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export function AnalyticsPanel({ data, loading }: { data: AnalyticsData | null; loading?: boolean }) {
  if (loading) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3 animate-pulse">
        <div className="grid grid-cols-3 gap-4">
          {[0, 1, 2].map(i => (
            <div key={i} className="space-y-2.5">
              <div className="h-2.5 bg-gray-800 rounded w-20" />
              {[0, 1, 2].map(j => <div key={j} className="h-5 bg-gray-800 rounded" />)}
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (!data) return null;

  const totalDecided = data.by_grade.reduce((s, g) => s + g.wins + g.losses, 0);
  const totalOpen = data.by_grade.reduce((s, g) => s + g.open, 0);
  if (totalDecided === 0 && totalOpen === 0) return null;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 sm:gap-0 sm:divide-x sm:divide-gray-800/60">
        <div className="sm:pr-4">
          <GradeSection rows={data.by_grade} />
        </div>
        <div className="sm:px-4">
          <DirectionSection rows={data.by_direction} avgHold={data.avg_hold_hours} />
        </div>
        <div className="sm:pl-4">
          <SymbolSection rows={data.by_symbol} />
        </div>
      </div>
    </div>
  );
}
