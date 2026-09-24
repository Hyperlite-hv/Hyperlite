// Alerts derived on the client from the states already loaded (there is no alerts API yet:
// improvement A-14). Pure function, unit tested.
export function deriveAlerts({ nodes = [], vms = [], storagePools = [], tasks = [] }) {
  const out = [];
  for (const n of nodes) if (n.etat !== "online") out.push({ id: `node:${n.id}`, level: "offline", text: `${n.nom}`, kind: "node-offline", target: { type: "node", id: n.id } });
  for (const v of vms) {
    if (v.etat === "plante") out.push({ id: `vm:${v.node}:${v.nom}`, level: "danger", text: v.nom, kind: "vm-crashed", target: { type: "vm", id: v.nom } });
    else if (v.etat === "bloque") out.push({ id: `vm:${v.node}:${v.nom}`, level: "warning", text: v.nom, kind: "vm-blocked", target: { type: "vm", id: v.nom } });
  }
  for (const p of storagePools) {
    const used = p.capacite_go ? ((p.capacite_go - (p.disponible_go ?? p.capacite_go)) / p.capacite_go) * 100 : 0;
    if (p.etat === "degrade" || p.etat === "inaccessible") out.push({ id: `pool:${p.node}:${p.nom}`, level: p.etat === "inaccessible" ? "danger" : "warning", text: p.nom, kind: "pool-state", target: { type: "node", id: p.node, tab: "disk" } });
    else if (used >= 90) out.push({ id: `pool:${p.node}:${p.nom}`, level: "danger", text: `${p.nom} ${Math.round(used)}%`, kind: "pool-full", target: { type: "node", id: p.node, tab: "disk" } });
    else if (used >= 80) out.push({ id: `pool:${p.node}:${p.nom}`, level: "warning", text: `${p.nom} ${Math.round(used)}%`, kind: "pool-high", target: { type: "node", id: p.node, tab: "disk" } });
  }
  for (const t of tasks.filter((x) => x.statut === "echec").slice(0, 5)) out.push({ id: `task:${t.id}`, level: "danger", text: `${t.type} ${t.cible || ""}`.trim(), kind: "task-failed", target: null, detail: t.erreur });
  const order = { danger: 0, warning: 1, offline: 2 };
  return out.sort((a, b) => order[a.level] - order[b.level]);
}

// Global health for the top bar badge: critical > attention (warnings, offline nodes) > healthy.
export function summarizeHealth(alerts) {
  const critical = alerts.filter((a) => a.level === "danger").length;
  const attention = alerts.length - critical;
  return { level: critical ? "critical" : attention ? "attention" : "ok", critical, attention, total: alerts.length };
}
