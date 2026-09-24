import { stateInfo, severity } from "../lib/enums";
import { normalize } from "../lib/format";

// Pure functions: inventory data -> tree rows -> pruned rows -> flat visible rows.
// Row shape: { key, kind, label, sub, count, info, badge, selection, open, children, hay, sev, problems }

function vmRow(vm, nodeName, keyPrefix, t, showNode) {
  const info = stateInfo("vm", vm.etat);
  return {
    key: `${keyPrefix}vm:${vm.node}:${vm.nom}`,
    kind: "vm", label: vm.nom,
    sub: [vm.ip || t(info.key).toLowerCase(), showNode ? nodeName : null].filter(Boolean).join(" · "),
    info, badge: null, count: null, children: [],
    selection: { type: "vm", id: vm.nom },
    resource: vm,
    hay: normalize([vm.nom, vm.ip, vm.os, vm.etat, t(info.key), nodeName, vm.uuid].filter(Boolean).join(" ")),
  };
}

function containerRow(c, nodeName, keyPrefix, t) {
  const info = stateInfo("container", c.etat);
  return {
    key: `${keyPrefix}ct:${c.nom}`, kind: "container", label: c.nom, sub: c.ip || t(info.key).toLowerCase(), info,
    count: null, children: [], selection: null, open: { type: "datacenter", id: null, tab: "containers" },
    resource: c, hay: normalize([c.nom, c.ip, c.etat, t(info.key), nodeName].filter(Boolean).join(" ")),
  };
}

function poolRow(p, node, t) {
  const info = stateInfo("pool", p.etat);
  const used = p.capacite_go && p.disponible_go != null ? Math.round(((p.capacite_go - p.disponible_go) / p.capacite_go) * 100) : null;
  return {
    key: `sp:${node.id}:${p.nom}`, kind: "storage", label: p.nom,
    sub: [p.type, used != null ? `${used}%` : null].filter(Boolean).join(" · "), info, count: null, children: [],
    selection: null, open: { type: "node", id: node.id, tab: "disk" },
    hay: normalize([p.nom, p.type, p.etat, t(info.key), node.nom].join(" ")),
  };
}

function networkRow(n, node, t) {
  const info = { key: n.actif ? "state.active" : "state.inactive", shape: n.actif ? "dot" : "square", tone: n.actif ? "success" : "offline" };
  return {
    key: `nw:${node.id}:${n.nom}`, kind: "network", label: n.nom, sub: n.pont ? `bridge ${n.pont}` : n.type, info, count: null, children: [],
    selection: null, open: { type: "datacenter", id: null, tab: "reseau" },
    hay: normalize([n.nom, n.pont, n.type, t(info.key)].filter(Boolean).join(" ")),
  };
}

function category(id, nodeId, label, children, t) {
  return {
    key: `cat:${id}:${nodeId}`, kind: "category", label, sub: null, info: null, count: children.length,
    selection: null, children, emptyLabel: t("inv.emptyCategory"), hay: normalize(label),
  };
}

function aggregate(row) {
  let worst = row.info ? severity(row.info) : 0;
  let danger = 0, warning = 0;
  if (row.info && (row.kind === "vm" || row.kind === "container" || row.kind === "storage" || row.kind === "network" || row.kind === "node")) {
    if (row.info.tone === "danger") danger += 1;
    if (row.info.tone === "warning") warning += 1;
  }
  for (const c of row.children) {
    const a = aggregate(c);
    worst = Math.max(worst, a.sev); danger += a.problems.danger; warning += a.problems.warning;
  }
  row.sev = worst; row.problems = { danger, warning };
  return row;
}

export function buildServerTree({ nodes, vms, containers, storagePools, networks }, t) {
  const nodeRows = nodes.map((n) => {
    const nvms = vms.filter((v) => v.node === n.id).map((v) => vmRow(v, n.nom, "", t, false));
    const isLocal = n.id === "local";
    const cts = isLocal ? (containers || []).map((c) => containerRow(c, n.nom, "", t)) : [];
    const pools = storagePools.filter((p) => p.node === n.id).map((p) => poolRow(p, n, t));
    const nets = isLocal ? (networks || []).map((x) => networkRow(x, n, t)) : [];
    const info = stateInfo("node", n.etat);
    return {
      key: `node:${n.id}`, kind: "node", label: n.nom, sub: null, badge: isLocal ? t("inv.local") : null, info, count: null,
      selection: { type: "node", id: n.id }, resource: n,
      children: [
        category("vms", n.id, t("inv.vms"), nvms, t),
        ...(isLocal ? [category("containers", n.id, t("inv.containers"), cts, t)] : []),
        category("storage", n.id, t("inv.storage"), pools, t),
        ...(isLocal ? [category("networks", n.id, t("inv.networks"), nets, t)] : []),
      ],
      hay: normalize([n.nom, n.ip, n.etat, t(info.key)].filter(Boolean).join(" ")),
    };
  });
  const root = {
    key: "dc", kind: "dc", label: t("inv.datacenter"), sub: null, info: null, count: null,
    selection: { type: "datacenter", id: null }, children: nodeRows, hay: normalize(t("inv.datacenter")),
  };
  root.sub = nodes.length === 1 ? t("inv.node1") : t("inv.nodes", { n: nodes.length });
  return [aggregate(root)];
}

// Pool mode = real resource pools (membership from GET /pools, admin only).
export function buildPoolTree({ nodes, vms, containers, pools }, t) {
  const nodeName = (id) => nodes.find((n) => n.id === id)?.nom || id;
  const inPool = new Set();
  const groups = (pools || []).map((p) => {
    const members = vms.filter((v) => (p.vms || []).includes(v.nom));
    members.forEach((v) => inPool.add(`${v.node}:${v.nom}`));
    return {
      key: `pool:${p.id}`, kind: "pool", label: p.name, sub: members.length === 1 ? t("inv.member1") : t("inv.members", { n: members.length }), info: null, count: null,
      selection: null, open: { type: "datacenter", id: null, tab: "permissions" },
      children: members.map((v) => vmRow(v, nodeName(v.node), `pool${p.id}:`, t, true)),
      hay: normalize(`${p.name} ${p.description || ""}`),
    };
  });
  const loose = [
    ...vms.filter((v) => !inPool.has(`${v.node}:${v.nom}`)).map((v) => vmRow(v, nodeName(v.node), "un:", t, true)),
    ...(containers || []).map((c) => containerRow(c, nodeName("local"), "un:", t)),
  ];
  groups.push({
    key: "pool:unassigned", kind: "pool", label: t("inv.unassigned"), sub: loose.length === 1 ? t("inv.member1") : t("inv.members", { n: loose.length }), info: null, count: null,
    selection: null, children: loose, hay: normalize(t("inv.unassigned")),
  });
  const root = { key: "dc", kind: "dc", label: t("inv.datacenter"), sub: null, info: null, count: null, selection: { type: "datacenter", id: null }, children: groups, hay: normalize(t("inv.datacenter")) };
  return [aggregate(root)];
}

const CONTAINER_KINDS = new Set(["dc", "node", "category", "pool"]);

// Keeps rows that match, and their ancestors. A matching container row (node, pool...) keeps its
// whole subtree so searching a node name shows what is inside it.
export function filterTree(rows, query) {
  const q = normalize(query.trim());
  if (!q) return { rows, matches: 0 };
  let matches = 0;
  const walk = (row) => {
    const self = row.hay.includes(q);
    if (self && CONTAINER_KINDS.has(row.kind) && row.kind !== "dc") {
      const count = (r) => (CONTAINER_KINDS.has(r.kind) ? 0 : 1) + r.children.reduce((a, c) => a + count(c), 0);
      matches += count(row);
      return row;
    }
    const kids = row.children.map(walk).filter(Boolean);
    if (self && !CONTAINER_KINDS.has(row.kind)) { matches += 1; return { ...row, children: kids }; }
    if (kids.length) return { ...row, children: kids };
    return null;
  };
  return { rows: rows.map(walk).filter(Boolean), matches };
}

export function defaultOpen(row, level) {
  if (row.kind === "dc" || row.kind === "node") return true;
  if (row.kind === "category") return row.key.startsWith("cat:vms:");
  return level < 1;
}

// Flat list of visible rows (flat "tree" pattern: aria-level/setsize/posinset on each treeitem).
export function flatten(rows, isOpen, level = 1, parentKey = null, out = []) {
  rows.forEach((row, i) => {
    out.push({ row, level, parentKey, posinset: i + 1, setsize: rows.length, expandable: row.children.length > 0 });
    if (row.children.length && isOpen(row, level)) flatten(row.children, isOpen, level + 1, row.key, out);
  });
  return out;
}

export function highlight(label, query) {
  const q = normalize(query.trim());
  if (!q) return [label];
  const nl = normalize(label);
  if (nl.length !== label.length) return [label];
  const i = nl.indexOf(q);
  if (i < 0) return [label];
  return [label.slice(0, i), { hit: label.slice(i, i + q.length) }, label.slice(i + q.length)];
}
