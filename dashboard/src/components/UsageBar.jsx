// Small usage bar + percentage (nodes table). `pct` null/undefined -> "n/a" (data
// not yet exposed), the same convention as the rest of the app (see
// DatacenterSummaryTab).
export default function UsageBar({ pct, color = "#4F46E5", width = 54 }) {
  if (pct == null) {
    return <span className="text-[11px] text-anthracite-400">n/a</span>;
  }
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 shrink-0 overflow-hidden rounded-full bg-anthracite-900" style={{ width }}>
        <div className="h-full rounded-full" style={{ width: `${clamped}%`, backgroundColor: color }} />
      </div>
      <span className="w-8 shrink-0 font-mono text-[11px] text-anthracite-300">{Math.round(clamped)}%</span>
    </div>
  );
}
