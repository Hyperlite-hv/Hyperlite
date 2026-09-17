// Petite barre d'usage + pourcentage (table des nœuds, refonte 2026-09-17).
// `pct` null/undefined -> "n/a" (donnee pas encore exposee), meme
// convention que le reste de l'app (voir DatacenterSummaryTab).
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
