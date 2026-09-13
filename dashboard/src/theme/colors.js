// Recharts a besoin de couleurs CSS explicites (il ne lit pas les classes Tailwind).
// Garder ces valeurs synchronisees avec tailwind.config.js si la palette change.
export const chartColors = {
  cpu: "#4f8cff",
  ram: "#ff9f43",
  disk: "#3fb950",
  netIn: "#4f8cff",
  netOut: "#c084fc",
  grid: "#2c303a",
  axis: "#7a8194",
};

export const statusColors = {
  actif: "#3fb950",
  online: "#3fb950",
  running: "#3fb950",
  arrete: "#7a8194",
  stopped: "#7a8194",
  avertissement: "#e3a008",
  warning: "#e3a008",
  erreur: "#e5484d",
  error: "#e5484d",
  // Etats reels d'un domaine libvirt (voir STATE_NAMES, app/routers/vms.py
  // et app/routers/containers.py) au-dela du sous-ensemble actif/arrete
  // deja couvert ci-dessus.
  en_pause: "#e3a008",
  suspendu: "#e3a008",
  bloque: "#e5484d",
  en_arret: "#7a8194",
  plante: "#e5484d",
  inconnu: "#7a8194",
};

export function statusColor(etat) {
  return statusColors[etat] || statusColors.arrete;
}
