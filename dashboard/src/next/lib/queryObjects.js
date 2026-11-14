import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchContainers, fetchPools } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { metaOf, useMetaStore } from "./meta";

// What a search by query runs on (lib/query.js): every VM, container and node this account can see, with its tags and
// pools. Shared by the search page and the dashboards' query tiles; pools and containers are read once a minute.
let shared = { at: 0, pools: [], containers: [], promise: null };
function extras() {
  if (shared.promise && Date.now() - shared.at < 60000) return shared.promise;
  shared.at = Date.now();
  shared.promise = Promise.all([fetchPools().catch(() => []), fetchContainers().catch(() => [])])
    .then(([pools, containers]) => ({ pools: Array.isArray(pools) ? pools : [], containers: Array.isArray(containers) ? containers : [] }));
  return shared.promise;
}

export function useQueryObjects() {
  const { vms, nodes } = useInfraStore(useShallow((s) => ({ vms: s.vms, nodes: s.nodes })));
  const byKey = useMetaStore((s) => s.byKey);
  const loadMeta = useMetaStore((s) => s.load);
  const [more, setMore] = useState({ pools: [], containers: [] });
  useEffect(() => { loadMeta(); let live = true; extras().then((x) => { if (live) setMore(x); }); return () => { live = false; }; }, [loadMeta]);
  return useMemo(() => {
    const { pools, containers } = more;
    const nodeName = (id) => nodes.find((n) => n.id === id)?.name || id;
    const poolsOf = (name) => pools.filter((p) => (p.vms || []).includes(name) || (p.dynamic_vms || []).includes(name)).map((p) => p.name);
    return [
      ...vms.map((v) => ({ kind: "vm", name: v.name, state: v.state, node: v.node, nodeName: nodeName(v.node), tags: metaOf(byKey, "vm", v.node, v.name)?.tags || [], pools: poolsOf(v.name), vcpu: v.vcpu, memory_mb: v.memory_mb, os: v.os, ip: v.ip, raw: v })),
      ...containers.map((c) => ({ kind: "container", name: c.name, state: c.state, node: "local", nodeName: nodeName("local"), tags: metaOf(byKey, "container", "local", c.name)?.tags || [], pools: [], vcpu: c.vcpu, memory_mb: c.memory_mb, ip: c.ip })),
      ...nodes.map((n) => ({ kind: "node", name: n.name, state: n.state, node: n.id, nodeName: n.name, tags: metaOf(byKey, "node", null, n.id)?.tags || [], pools: [], vcpu: n.cpu_coeurs, memory_mb: n.memoire_totale_mo, ip: n.ip, id: n.id })),
    ];
  }, [vms, nodes, more, byKey]);
}

// Counts of the found objects by one field, largest first. An object with several pools or tags counts in each.
export function groupBy(found, field) {
  const counts = new Map();
  const add = (k) => counts.set(k, (counts.get(k) || 0) + 1);
  for (const o of found) {
    if (field === "pool") (o.pools.length ? o.pools : ["—"]).forEach(add);
    else if (field === "tag") (o.tags.length ? o.tags : ["—"]).forEach(add);
    else if (field === "node") add(o.nodeName || "—");
    else add(o[field] || "—");
  }
  return [...counts].map(([key, n]) => ({ key, n })).sort((a, b) => b.n - a.n || String(a.key).localeCompare(String(b.key)));
}

export const openObject = (navigateTo, o) => (o.kind === "vm" ? navigateTo("vm", o.name, "summary") : o.kind === "container" ? navigateTo("container", o.name, "summary") : navigateTo("node", o.id, "summary"));
