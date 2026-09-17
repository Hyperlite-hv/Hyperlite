// Recharts a besoin de couleurs CSS explicites (il ne lit pas les classes Tailwind).
// Garder ces valeurs synchronisees avec tailwind.config.js si la palette change.
// Refonte 2026-09-17 : indigo/blanc plutot que le violet-marine precedent.
export const chartColors = {
  cpu: "#4F46E5",
  ram: "#D97706",
  disk: "#16A34A",
  netIn: "#4F46E5",
  netOut: "#A855F7",
  grid: "#E5E7EB",
  axis: "#9CA3AF",
};

export const statusColors = {
  actif: "#16A34A",
  online: "#16A34A",
  running: "#16A34A",
  arrete: "#6B7280",
  stopped: "#6B7280",
  avertissement: "#D97706",
  warning: "#D97706",
  erreur: "#DC2626",
  error: "#DC2626",
  // Etats reels d'un domaine libvirt (voir STATE_NAMES, app/routers/vms.py
  // et app/routers/containers.py) au-dela du sous-ensemble actif/arrete
  // deja couvert ci-dessus.
  en_pause: "#D97706",
  suspendu: "#D97706",
  bloque: "#DC2626",
  en_arret: "#6B7280",
  plante: "#DC2626",
  inconnu: "#6B7280",
};

export function statusColor(etat) {
  return statusColors[etat] || statusColors.arrete;
}
