import { statusColor } from "../theme/colors";

const LABELS = {
  actif: "Running", online: "Online", running: "Running",
  arrete: "Stopped", stopped: "Stopped",
  avertissement: "Warning", warning: "Warning",
  erreur: "Error", error: "Error",
  // Real states of a libvirt domain (see STATE_NAMES, app/routers/vms.py and
  // app/routers/containers.py) beyond the active/stopped subset.
  en_pause: "Paused", suspendu: "Suspended", bloque: "Blocked",
  en_arret: "Shutting down", plante: "Crashed", inconnu: "Unknown",
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
      {showLabel && <span className="text-foreground/90 text-xs">{LABELS[etat] || etat}</span>}
    </span>
  );
}
