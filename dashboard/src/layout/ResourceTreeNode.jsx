import { useState } from "react";
import { ChevronRight, ChevronDown, Database, Server, Box, Layers, HardDrive } from "lucide-react";
import { statusColor } from "../theme/colors";
import { useInfraStore } from "../store/useInfraStore";

const ICONS = { datacenter: Database, node: Server, group: Layers, vm: Box, container: Box, storage: HardDrive };
const SELECTABLE = new Set(["datacenter", "node", "vm", "container", "storage"]);

export default function ResourceTreeNode({ node, depth = 0 }) {
  const [expanded, setExpanded] = useState(depth < 2);
  const selection = useInfraStore((s) => s.selection);
  const select = useInfraStore((s) => s.select);

  const hasChildren = node.children && node.children.length > 0;
  const Icon = ICONS[node.type] || Box;
  const isSelected = SELECTABLE.has(node.type) && selection.type === node.type && selection.id === node.id;

  return (
    <div>
      <div
        className={`flex items-center gap-1.5 rounded px-1.5 py-1 text-sm cursor-pointer select-none ${
          isSelected ? "bg-accent-blue/15 text-anthracite-100" : "text-anthracite-200 hover:bg-anthracite-700"
        }`}
        style={{ paddingLeft: 6 + depth * 14 }}
        onClick={() => {
          if (hasChildren) setExpanded((e) => !e);
          if (SELECTABLE.has(node.type)) select(node.type, node.id);
        }}
      >
        {hasChildren ? (
          expanded ? <ChevronDown size={13} className="text-anthracite-400 shrink-0" /> : <ChevronRight size={13} className="text-anthracite-400 shrink-0" />
        ) : (
          <span className="w-[13px] shrink-0" />
        )}
        <Icon size={14} className="text-anthracite-300 shrink-0" />
        {node.etat && (
          <span
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{ backgroundColor: statusColor(node.etat) }}
          />
        )}
        <span className="truncate">{node.label}</span>
        {node.badge != null && (
          <span className="ml-auto text-[11px] text-anthracite-400 shrink-0">{node.badge}</span>
        )}
      </div>
      {hasChildren && expanded && (
        <div>
          {node.children.map((child) => (
            <ResourceTreeNode key={child.key} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
