import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import ResourceTreeNode from "./ResourceTreeNode";
import { useInfraStore } from "../store/useInfraStore";

const FILTERS = [
  { id: "server", label: "Server" },
  { id: "pool", label: "Pool" },
];

function vmNode(v) {
  return { key: `vm-${v.nom}`, label: v.nom, type: "vm", id: v.nom, etat: v.etat };
}
function storageNode(p) {
  return { key: `st-${p.nom}`, label: p.nom, type: "storage", id: p.nom, etat: p.etat };
}

function buildByServer(nodes, vms, storagePools) {
  return nodes.map((n) => ({
    key: `node-${n.id}`, label: n.nom, type: "node", id: n.id, etat: n.etat,
    children: [
      ...storagePools.filter((p) => p.node === n.id).map(storageNode),
      ...vms.filter((v) => v.node === n.id).map(vmNode),
    ],
  }));
}

function buildByPool(vms, storagePools) {
  return storagePools.map((p) => ({
    key: `poolgroup-${p.nom}`, label: `${p.nom} (${p.node})`, type: "group", id: null,
    children: vms.filter((v) => v.node === p.node).map(vmNode),
  }));
}

// Prunes the tree: a node survives if its label matches or if a descendant survives.
function filterTree(node, query) {
  if (!query) return node;
  const q = query.toLowerCase();
  const children = (node.children || []).map((c) => filterTree(c, query)).filter(Boolean);
  const selfMatch = node.label.toLowerCase().includes(q);
  if (!selfMatch && children.length === 0) return null;
  return { ...node, children };
}

export default function ResourceTree() {
  const { nodes, vms, storagePools, treeFilter, setTreeFilter, searchQuery } = useInfraStore(useShallow((s) => ({
    nodes: s.nodes, vms: s.vms, storagePools: s.storagePools,
    treeFilter: s.treeFilter, setTreeFilter: s.setTreeFilter, searchQuery: s.searchQuery,
  })));

  const tree = useMemo(() => {
    const children = treeFilter === "pool" ? buildByPool(vms, storagePools) : buildByServer(nodes, vms, storagePools);
    const root = { key: "dc", label: "Datacenter", type: "datacenter", id: null, children };
    return filterTree(root, searchQuery) || { ...root, children: [] };
  }, [nodes, vms, storagePools, treeFilter, searchQuery]);

  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-white/6 mt-1 pt-2">
      <div className="flex gap-0.5 mx-2.5 mb-1.5 p-0.5 rounded-md bg-black/20">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setTreeFilter(f.id)}
            className={`flex-1 rounded px-2 py-1.5 text-xs font-medium transition-colors ${
              treeFilter === f.id ? "bg-chrome-700 text-chrome-100" : "text-chrome-400 hover:text-chrome-100"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-1.5 pt-0.5">
        <div role="tree" aria-label="Resources">
          <ResourceTreeNode node={tree} />
        </div>
      </div>
    </div>
  );
}
