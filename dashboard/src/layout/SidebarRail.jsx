import {
  LayoutDashboard, Activity, Box, HardDrive, Network, Layers,
  CalendarClock, PackageOpen, ShieldCheck, Workflow, Server, ScrollText, LifeBuoy, Bell, ClipboardCheck,
} from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

// Proxmox VE-style navigation rail (modelled on DATACENTER_TABS, CentralPanel.jsx:
// one entry per section actually available in Hyperlite, not the full Proxmox
// list, which does not exist here). Purely additive: it does not replace the
// Datacenter > Node > VM tree (ResourceTree, just below in the same column) which
// remains the existing navigation/VM shortcuts. This rail only jumps directly to a
// given Datacenter tab without going through the tree.
const TOP_ITEMS = [
  { tab: "summary", label: "Dashboard", Icon: LayoutDashboard },
  { tab: "activity", label: "Recent activity", Icon: Activity },
];
const GROUPS = [
  {
    label: "Resources",
    items: [
      { tab: "containers", label: "Containers", Icon: Box },
      { tab: "storage", label: "Storage", Icon: HardDrive },
      { tab: "reseau", label: "Network", Icon: Network },
      { tab: "templates", label: "Templates / ISO", Icon: Layers },
      { tab: "nodes", label: "Nodes", Icon: Server },
      { tab: "ha", label: "HA", Icon: LifeBuoy },
      { tab: "compat", label: "Compatibility", Icon: ClipboardCheck },
    ],
  },
  {
    label: "Operations",
    items: [
      { tab: "backups", label: "Backups", Icon: CalendarClock },
      { tab: "exports", label: "Exports", Icon: PackageOpen },
      { tab: "automation", label: "Automation", Icon: Workflow },
      { tab: "permissions", label: "Permissions", Icon: ShieldCheck },
      { tab: "journal", label: "Journal", Icon: ScrollText },
      { tab: "notifications", label: "Notifications", Icon: Bell },
    ],
  },
];

function NavItem({ tab, label, Icon }) {
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const closeMobileSidebar = useInfraStore((s) => s.closeMobileSidebar);
  const isActive = useInfraStore((s) => s.selection.type === "datacenter" && s.activeTab === tab);

  return (
    <button
      onClick={() => { navigateTo("datacenter", null, tab); closeMobileSidebar(); }}
      className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12.5px] font-semibold transition-colors ${
        isActive
          ? "bg-accent-blue text-white shadow-[0_4px_14px_rgba(79,70,229,0.35)]"
          : "text-chrome-400 hover:bg-white/6 hover:text-chrome-100"
      }`}
    >
      <Icon size={16} className="shrink-0" />
      <span className="truncate">{label}</span>
    </button>
  );
}

export default function SidebarRail() {
  return (
    <div className="flex shrink-0 flex-col gap-0.5 p-2.5 pb-1">
      {TOP_ITEMS.map((item) => <NavItem key={item.tab} {...item} />)}
      {GROUPS.map((group) => (
        <div key={group.label} className="mt-1">
          <div className="px-3 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-chrome-400/70">
            {group.label}
          </div>
          <div className="flex flex-col gap-0.5">
            {group.items.map((item) => <NavItem key={item.tab} {...item} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
