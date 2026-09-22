// Progress bar driven by a real percentage (used by TaskLogPanel and
// IsoUploadDropzone, which have a real `value` to display).
//
// `indeterminate`: for operations where no real percentage exists on the backend
// (e.g. snapshot creation/restore: verified that libvirt exposes no progress stat
// for this precise operation), rather than inventing a fake value that would
// advance arbitrarily. Shows an animated (pulsing) segment instead of a fill.
export default function ProgressBar({ value, statut = "en_cours", size = "md", indeterminate = false }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const height = size === "sm" ? "h-1.5" : "h-2.5";
  const colorClass =
    statut === "echec" ? "bg-status-error"
    : statut === "termine" ? "bg-status-running"
    : "bg-accent-blue";

  if (indeterminate) {
    return (
      <div className={`w-full ${height} rounded-full bg-muted overflow-hidden`}>
        <div className={`${height} w-2/5 ${colorClass} rounded-full animate-pulse`} />
      </div>
    );
  }

  return (
    <div className={`w-full ${height} rounded-full bg-muted overflow-hidden`}>
      <div
        className={`${height} ${colorClass} rounded-full transition-[width] duration-300 ease-out`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
