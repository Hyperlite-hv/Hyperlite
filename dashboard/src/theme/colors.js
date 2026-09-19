// Recharts needs explicit CSS colors (it does not read Tailwind classes). Keep
// these values in sync with tailwind.config.js if the palette changes.
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
  // Real states of a libvirt domain (see STATE_NAMES, app/routers/vms.py and
  // app/routers/containers.py) beyond the active/stopped subset already covered
  // above.
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
