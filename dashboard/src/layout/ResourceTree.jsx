import { useMemo } from "react";
import ResourceTreeNode from "./ResourceTreeNode";
import { useInfraStore } from "../store/useInfraStore";

const FILTERS = [
  { id: "server", label: "Serveur" },
  { id: "pool", label: "Pool" },
  { id: "tag", label: "Tag" },
];

function vmNode(v) {
  return { key: `vm-${v.nom}`, label: v.nom, type: "vm", id: v.nom, etat: v.etat };
}
function containerNode(c) {
  return { key: `ct-${c.nom}`, label: c.nom, type: "container", id: c.nom, etat: c.etat };
}
function storageNode(p) {
  return { key: `st-${p.nom}`, label: p.nom, type: "storage", id: p.nom, etat: p.etat };
}

function buildByServer(nodes, vms, containers, storagePools) {
  return nodes.map((n) => ({
    key: `node-${n.id}`, label: n.nom, type: "node", id: n.id, etat: n.etat,
    children: [
      ...storagePools.filter((p) => p.node === n.id).map(storageNode),
      ...vms.filter((v) => v.node === n.id).map(vmNode),
      ...containers.filter((c) => c.node === n.id).map(containerNode),
    ],
  }));
}

function buildByTag(vms, containers) {
  const tags = new Set();
  [...vms, ...containers].forEach((r) => (r.tags || []).forEach((t) => tags.add(t)));
  return [...tags].sort().map((tag) => ({
    key: `tag-${tag}`, label: tag, type: "group", id: null,
    children: [
      ...vms.filter((v) => (v.tags || []).includes(tag)).map(vmNode),
      ...containers.filter((c) => (c.tags || []).includes(tag)).map(containerNode),
    ],
  }));
}

function buildByPool(vms, containers, storagePools) {
  return storagePools.map((p) => ({
    key: `poolgroup-${p.nom}`, label: `${p.nom} (${p.node})`, type: "group", id: null,
    children: [
      ...vms.filter((v) => v.node === p.node).map(vmNode),
      ...containers.filter((c) => c.node === p.node).map(containerNode),
    ],
  }));
}

// Elague l'arbre : un noeud survit si son libelle matche ou si un descendant survit.
function filterTree(node, query) {
  if (!query) return node;
  const q = query.toLowerCase();
  const children = (node.children || []).map((c) => filterTree(c, query)).filter(Boolean);
  const selfMatch = node.label.toLowerCase().includes(q);
  if (!selfMatch && children.length === 0) return null;
  return { ...node, children };
}

export default function ResourceTree() {
  const { nodes, vms, containers, storagePools, treeFilter, setTreeFilter, searchQuery } = useInfraStore((s) => ({
    nodes: s.nodes, vms: s.vms, containers: s.containers, storagePools: s.storagePools,
    treeFilter: s.treeFilter, setTreeFilter: s.setTreeFilter, searchQuery: s.searchQuery,
  }));

  const tree = useMemo(() => {
    let children;
    if (treeFilter === "tag") children = buildByTag(vms, containers);
    else if (treeFilter === "pool") children = buildByPool(vms, containers, storagePools);
    else children = buildByServer(nodes, vms, containers, storagePools);

    const root = { key: "dc", label: "Datacenter", type: "datacenter", id: null, children };
    return filterTree(root, searchQuery) || { ...root, children: [] };
  }, [nodes, vms, containers, storagePools, treeFilter, searchQuery]);

  return (
    <div className="flex h-full flex-col bg-anthracite-800 border-r border-anthracite-600">
      <div className="flex gap-1 p-2 border-b border-anthracite-600">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setTreeFilter(f.id)}
            className={`flex-1 rounded px-2 py-1 text-xs font-medium transition-colors ${
              treeFilter === f.id ? "bg-accent-blue text-white" : "bg-anthracite-700 text-anthracite-300 hover:text-anthracite-100"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-1.5">
        <ResourceTreeNode node={tree} />
      </div>
    </div>
  );
}
