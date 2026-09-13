// Barre de progression pilotee par un pourcentage reel (utilisee par
// TaskLogPanel et IsoUploadDropzone, qui ont un vrai `value` a afficher).
//
// `indeterminate` : pour les operations ou aucun pourcentage reel n'existe
// cote backend (ex. creation/restauration de snapshot -- verifie sur ce host
// que libvirt n'expose aucune stat de progression pour cette operation
// precise) plutot que d'inventer une fausse valeur qui avancerait de facon
// arbitraire. Affiche un segment anime (pulse) au lieu d'un remplissage.
export default function ProgressBar({ value, statut = "en_cours", size = "md", indeterminate = false }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const height = size === "sm" ? "h-1.5" : "h-2.5";
  const colorClass =
    statut === "echec" ? "bg-status-error"
    : statut === "termine" ? "bg-status-running"
    : "bg-accent-blue";

  if (indeterminate) {
    return (
      <div className={`w-full ${height} rounded-full bg-anthracite-600 overflow-hidden`}>
        <div className={`${height} w-2/5 ${colorClass} rounded-full animate-pulse`} />
      </div>
    );
  }

  return (
    <div className={`w-full ${height} rounded-full bg-anthracite-600 overflow-hidden`}>
      <div
        className={`${height} ${colorClass} rounded-full transition-[width] duration-300 ease-out`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
