// Vraie barre de progression pilotee par un pourcentage reel (pas une animation
// indeterminee) : utilisee par TaskLogPanel et IsoUploadDropzone.
export default function ProgressBar({ value, statut = "en_cours", size = "md" }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const height = size === "sm" ? "h-1.5" : "h-2.5";
  const colorClass =
    statut === "echec" ? "bg-status-error"
    : statut === "termine" ? "bg-status-running"
    : "bg-accent-blue";

  return (
    <div className={`w-full ${height} rounded-full bg-anthracite-600 overflow-hidden`}>
      <div
        className={`${height} ${colorClass} rounded-full transition-[width] duration-300 ease-out`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
