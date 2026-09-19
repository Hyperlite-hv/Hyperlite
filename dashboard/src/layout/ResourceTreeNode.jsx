import { useState } from "react";
import { ChevronRight, ChevronDown, Database, Server, Box, Layers, HardDrive } from "lucide-react";
import { statusColor } from "../theme/colors";
import { useInfraStore } from "../store/useInfraStore";

const ICONS = { datacenter: Database, node: Server, group: Layers, vm: Box, storage: HardDrive };
const SELECTABLE = new Set(["datacenter", "node", "vm", "storage"]);

export default function ResourceTreeNode({ node, depth = 0 }) {
  const [expanded, setExpanded] = useState(depth < 2);
  const selection = useInfraStore((s) => s.selection);
  const select = useInfraStore((s) => s.select);
  const closeMobileSidebar = useInfraStore((s) => s.closeMobileSidebar);

  const hasChildren = node.children && node.children.length > 0;
  const Icon = ICONS[node.type] || Box;
  const isSelected = SELECTABLE.has(node.type) && selection.type === node.type && selection.id === node.id;

  const activate = () => {
    if (hasChildren) setExpanded((e) => !e);
    if (SELECTABLE.has(node.type)) select(node.type, node.id);
    // Closes the mobile drawer only on a real leaf (VM/pool, or a node without
    // children); otherwise a simple expand/collapse of "Datacenter"/node would close
    // the drawer before the user could pick a child.
    if (SELECTABLE.has(node.type) && !hasChildren) closeMobileSidebar();
  };

  return (
    <div role="none">
      <div
        role="treeitem"
        tabIndex={0}
        aria-selected={isSelected}
        aria-expanded={hasChildren ? expanded : undefined}
        aria-level={depth + 1}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
          else if (e.key === "ArrowRight" && hasChildren && !expanded) setExpanded(true);
          else if (e.key === "ArrowLeft" && hasChildren && expanded) setExpanded(false);
        }}
        className={`flex items-center gap-1.5 rounded px-1.5 py-1 text-sm cursor-pointer select-none ${
          isSelected ? "bg-chrome-700 text-chrome-100 font-semibold" : "text-chrome-100/80 hover:bg-chrome-700/60"
        }`}
        style={{ paddingLeft: 6 + depth * 14 }}
        onClick={activate}
      >
        {hasChildren ? (
          expanded ? <ChevronDown size={13} className="text-chrome-400 shrink-0" /> : <ChevronRight size={13} className="text-chrome-400 shrink-0" />
        ) : (
          <span className="w-[13px] shrink-0" />
        )}
        <Icon size={14} className="text-chrome-400 shrink-0" />
        {node.etat && (
          <span
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{ backgroundColor: statusColor(node.etat) }}
          />
        )}
        <span className="truncate">{node.label}</span>
        {node.badge != null && (
          <span className="ml-auto text-[11px] text-chrome-400 shrink-0">{node.badge}</span>
        )}
      </div>
      {hasChildren && expanded && (
        <div role="group">
          {node.children.map((child) => (
            <ResourceTreeNode key={child.key} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
