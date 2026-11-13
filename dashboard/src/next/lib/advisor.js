// Recommendations: rules over what the console already knows (inventory, backup schedules, accounts, tokens,
// notification channels). Each one says what is at risk, how much it matters and which resources it concerns.
// Pure: the page gathers the data, the rules only read it, so they are tested without a server.
//
// A source that could not be read is passed as null: its rules are skipped rather than reporting every resource.

export const CATEGORIES = ["reliability", "security", "performance", "efficiency", "operations"];
export const IMPACTS = ["high", "medium", "low"];
const RANK = { high: 0, medium: 1, low: 2 };

const vmKey = (v) => (v.node && v.node !== "local" ? `${v.name}@${v.node}` : v.name);
const vmRef = (v) => ({ type: "vm", id: vmKey(v), name: v.name, node: v.node });
const nodeRef = (n) => ({ type: "node", id: n.id, name: n.name });
const running = (v) => v.state === "active";

// Thresholds, named so the page can show them next to the rule.
export const LIMITS = { poolHigh: 90, poolMedium: 80, nodeMemory: 90, vcpuPerCore: 4 };

export function recommendations({ nodes = [], vms = [], pools = [], scheduled = null, users = null, tokens = null, channels = null }) {
  const out = [];
  const add = (id, category, impact, resources, extra = {}) => { if (resources.length) out.push({ id, category, impact, resources, ...extra }); };
  const machines = vms.filter((v) => !v.template);
  const online = nodes.filter((n) => n.state === "online");

  // Reliability
  add("node-offline", "reliability", "high", nodes.filter((n) => n.state && n.state !== "online").map(nodeRef), { go: { svc: "nodes" } });
  add("vm-crashed", "reliability", "high", machines.filter((v) => v.state === "crashed").map(vmRef));
  if (scheduled) add("vm-no-backup", "reliability", "high", machines.filter((v) => !scheduled.has(v.name)).map(vmRef), { go: { svc: "backups" } });
  add("vm-no-agent", "reliability", "medium", machines.filter((v) => running(v) && v.guest_agent && v.guest_agent !== "active").map(vmRef));
  if (nodes.length === 1 && machines.length > 0) add("single-node", "reliability", "medium", nodes.map(nodeRef), { go: { svc: "nodes", intent: "node" } });

  // Performance
  const memPct = (n) => (n.memoire_totale_mo > 0 ? (n.used_memory_mb / n.memoire_totale_mo) * 100 : 0);
  add("node-memory", "performance", "high", online.filter((n) => memPct(n) >= LIMITS.nodeMemory).map((n) => ({ ...nodeRef(n), value: Math.round(memPct(n)) })));
  const vcpuOn = (n) => machines.filter((v) => running(v) && (v.node || "local") === n.id).reduce((s, v) => s + (v.vcpu || 0), 0);
  add("node-vcpu", "performance", "medium", online.filter((n) => n.cpu_coeurs > 0 && vcpuOn(n) / n.cpu_coeurs > LIMITS.vcpuPerCore)
    .map((n) => ({ ...nodeRef(n), value: Math.round((vcpuOn(n) / n.cpu_coeurs) * 10) / 10 })), { go: { svc: "drs" } });
  const poolPct = (p) => (p.capacity_gb > 0 ? ((p.capacity_gb - (p.available_gb ?? p.capacity_gb)) / p.capacity_gb) * 100 : 0);
  const poolRef = (p) => ({ type: "pool", id: `${p.node}:${p.name}`, name: p.name, node: p.node, value: Math.round(poolPct(p)) });
  add("pool-full", "performance", "high", pools.filter((p) => poolPct(p) >= LIMITS.poolHigh).map(poolRef), { go: { svc: "storage" } });
  add("pool-filling", "performance", "medium", pools.filter((p) => poolPct(p) >= LIMITS.poolMedium && poolPct(p) < LIMITS.poolHigh).map(poolRef), { go: { svc: "storage" } });

  // Efficiency
  add("vm-stopped", "efficiency", "low", machines.filter((v) => v.state === "stopped" || v.state === "shutoff").map(vmRef), { go: { svc: "vms" } });

  // Security
  if (users) add("admin-no-2fa", "security", "high", users.filter((u) => u.role === "admin" && !u.totp_enabled).map((u) => ({ type: "user", id: u.username, name: u.username })), { go: { svc: "permissions" } });
  if (tokens) add("token-no-expiry", "security", "medium", tokens.filter((k) => !k.expires_at).map((k) => ({ type: "token", id: String(k.id), name: k.name })), { go: { svc: "api" } });

  // Operations
  if (channels === 0) add("no-channel", "operations", "medium", [{ type: "cluster", id: "cluster", name: "" }], { go: { svc: "notifications" } });

  return out.sort((a, b) => RANK[a.impact] - RANK[b.impact] || b.resources.length - a.resources.length);
}

// The recommendations that concern one resource (a VM's or a node's own page).
export function forResource(list, type, id) {
  return list.filter((r) => r.resources.some((x) => x.type === type && x.id === id));
}

// Snoozed and dismissed recommendations: { [id]: until (epoch ms) | "dismissed" }, kept in this browser.
export function isHidden(state, id, now = Date.now()) {
  const v = state?.[id];
  return v === "dismissed" || (typeof v === "number" && v > now);
}
