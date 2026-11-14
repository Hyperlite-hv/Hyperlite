import { create } from "zustand";

// A small query language over the inventory (docs/design/governance.md, section 14):
//   kind:vm state:stopped tag:prod pool:web node:antho vcpu>=4 mem>8 os:debian web
// Each term narrows the result; a bare word matches the name (or the IP). `-tag:prod` excludes. Comparisons take
// numbers (mem and disk in GB). Values may be quoted ("my pool").

const FIELDS = new Set(["kind", "name", "state", "tag", "pool", "node", "vcpu", "mem", "os", "ip"]);
const NUMERIC = new Set(["vcpu", "mem"]);

export function parseQuery(text) {
  const terms = [];
  const errors = [];
  const re = /(-?)([a-z]+)(>=|<=|:|>|<)("[^"]*"|\S+)|("[^"]*"|\S+)/gi;
  for (const m of (text || "").matchAll(re)) {
    if (m[5] !== undefined) { terms.push({ field: "text", op: ":", value: m[5].replace(/^"|"$/g, "").toLowerCase(), not: false }); continue; }
    const field = m[2].toLowerCase();
    const value = m[4].replace(/^"|"$/g, "").toLowerCase();
    if (!FIELDS.has(field)) { errors.push(field); continue; }
    if (m[3] !== ":" && !NUMERIC.has(field)) { errors.push(`${field}${m[3]}`); continue; }
    if (NUMERIC.has(field) && Number.isNaN(Number(value))) { errors.push(`${field}${m[3]}${value}`); continue; }
    terms.push({ field, op: m[3], value, not: m[1] === "-" });
  }
  return { terms, errors };
}

const STATES = { running: ["active", "running"], stopped: ["stopped", "inactive", "shutoff"], paused: ["paused"] };

function compare(a, op, b) {
  if (a == null) return false;
  return op === ">" ? a > b : op === "<" ? a < b : op === ">=" ? a >= b : op === "<=" ? a <= b : a === b;
}

function matches(obj, t) {
  const v = t.value;
  switch (t.field) {
    case "text": return obj.name.toLowerCase().includes(v) || (obj.ip || "").includes(v);
    case "name": return v.includes("*") ? new RegExp(`^${v.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")}$`).test(obj.name.toLowerCase()) : obj.name.toLowerCase().includes(v);
    case "kind": return obj.kind === v || (v === "ct" && obj.kind === "container");
    case "state": return (STATES[v] || [v]).includes((obj.state || "").toLowerCase());
    case "tag": return (obj.tags || []).includes(v);
    case "pool": return (obj.pools || []).some((p) => p.toLowerCase() === v);
    case "node": return (obj.nodeName || "").toLowerCase() === v || (obj.node || "").toLowerCase() === v;
    case "os": return (obj.os || "").toLowerCase().includes(v);
    case "ip": return (obj.ip || "").startsWith(v);
    case "vcpu": return compare(obj.vcpu, t.op === ":" ? "=" : t.op, Number(v));
    case "mem": return compare(obj.memory_mb != null ? obj.memory_mb / 1024 : null, t.op === ":" ? "=" : t.op, Number(v));
    default: return false;
  }
}

// `objects`: [{kind, name, state, node, nodeName, tags, pools, vcpu, memory_mb, os, ip}].
export function runQuery(objects, text) {
  const { terms, errors } = parseQuery(text);
  const found = objects.filter((o) => terms.every((t) => (t.not ? !matches(o, t) : matches(o, t))));
  return { found, errors, terms };
}

// The query the Search page shows: set by the palette ("Search: kind:vm tag:prod") before it opens the page.
export const useQueryStore = create((set) => ({ q: "", setQ: (q) => set({ q }) }));
