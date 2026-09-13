import { statusColor } from "../theme/colors";

const LABELS = {
  actif: "En marche", online: "En ligne", running: "En marche",
  arrete: "Arrêté", stopped: "Arrêté",
  avertissement: "Avertissement", warning: "Avertissement",
  erreur: "Erreur", error: "Erreur",
};

export default function StatusBadge({ etat, showLabel = true, size = "sm" }) {
  const color = statusColor(etat);
  const dotSize = size === "sm" ? "w-2 h-2" : "w-2.5 h-2.5";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`${dotSize} rounded-full shrink-0`}
        style={{ backgroundColor: color, boxShadow: `0 0 6px ${color}99` }}
      />
      {showLabel && <span className="text-anthracite-200 text-xs">{LABELS[etat] || etat}</span>}
    </span>
  );
}
