import { vmKey } from "./vmId";
// Alerts derived on the client from the states already loaded (there is no alerts API yet:
// improvement A-14). Pure function, unit tested.
export function deriveAlerts({ nodes = [], vms = [], storagePools = [], tasks = [] }) {
  const out = [];
  for (const n of nodes) if (n.state !== "online") out.push({ id: `node:${n.id}`, level: "offline", text: `${n.name}`, kind: "node-offline", target: { type: "node", id: n.id } });
  for (const v of vms) {
    if (v.state === "crashed") out.push({ id: `vm:${v.node}:${v.name}`, level: "danger", text: v.name, kind: "vm-crashed", target: { type: "vm", id: vmKey(v) } });
    else if (v.state === "blocked") out.push({ id: `vm:${v.node}:${v.name}`, level: "warning", text: v.name, kind: "vm-blocked", target: { type: "vm", id: vmKey(v) } });
  }
  for (const p of storagePools) {
    const used = p.capacity_gb ? ((p.capacity_gb - (p.available_gb ?? p.capacity_gb)) / p.capacity_gb) * 100 : 0;
    if (p.state === "degrade" || p.state === "inaccessible") out.push({ id: `pool:${p.node}:${p.name}`, level: p.state === "inaccessible" ? "danger" : "warning", text: p.name, kind: "pool-state", target: { type: "node", id: p.node, tab: "disk" } });
    else if (used >= 90) out.push({ id: `pool:${p.node}:${p.name}`, level: "danger", text: `${p.name} ${Math.round(used)}%`, kind: "pool-full", target: { type: "node", id: p.node, tab: "disk" } });
    else if (used >= 80) out.push({ id: `pool:${p.node}:${p.name}`, level: "warning", text: `${p.name} ${Math.round(used)}%`, kind: "pool-high", target: { type: "node", id: p.node, tab: "disk" } });
  }
  for (const t of tasks.filter((x) => x.status === "failed").slice(0, 5)) out.push({ id: `task:${t.id}`, level: "danger", text: `${t.type} ${t.target || ""}`.trim(), kind: "task-failed", target: null, detail: t.error });
  const order = { danger: 0, warning: 1, offline: 2 };
  return out.sort((a, b) => order[a.level] - order[b.level]);
}

// Global health for the top bar badge: critical > attention (warnings, offline nodes) > healthy.
export function summarizeHealth(alerts) {
  const critical = alerts.filter((a) => a.level === "danger").length;
  const attention = alerts.length - critical;
  return { level: critical ? "critical" : attention ? "attention" : "ok", critical, attention, total: alerts.length };
}
