import {
  LayoutDashboard, Activity, Box, HardDrive, Network, Layers,
  CalendarClock, PackageOpen, ShieldCheck, Workflow, Server, ScrollText, LifeBuoy, Bell, ClipboardCheck,
} from "lucide-react";
import HyperliteLogo from "../components/HyperliteLogo";
import ResourceTree from "./ResourceTree";
import { useInfraStore } from "../store/useInfraStore";
import {
  Sidebar as SidebarRoot,
  SidebarHeader,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  useSidebar,
} from "@/components/ui/sidebar";

// Same navigation model as before the redesign (Proxmox VE-style rail: one entry
// per Datacenter section, modelled on DATACENTER_TABS in CentralPanel.jsx), now
// hosted in a real shadcn Sidebar instead of a hand-rolled column. Since this rail
// covers every Datacenter tab, CentralPanel no longer renders a second, duplicate
// tab bar for the Datacenter level (see the "navigation" note there) — this is the
// single entry point for that level, the top tab bar in CentralPanel is reserved
// for Node/VM detail, which have no rail equivalent.
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
  const isActive = useInfraStore((s) => s.selection.type === "datacenter" && s.activeTab === tab);
  const { setOpenMobile } = useSidebar();

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        isActive={isActive}
        tooltip={label}
        onClick={() => { navigateTo("datacenter", null, tab); setOpenMobile(false); }}
      >
        <Icon />
        <span>{label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

export default function Sidebar() {
  return (
    <SidebarRoot collapsible="icon" className="border-sidebar-border">
      <SidebarHeader className="flex-row items-center gap-2.5 px-2 py-3">
        <HyperliteLogo size={28} />
        <div className="leading-tight group-data-[collapsible=icon]:hidden">
          <div className="text-[15px] font-extrabold tracking-tight text-sidebar-foreground">Hyperlite</div>
          <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-sidebar-foreground/60">Hypervisor</div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {TOP_ITEMS.map((item) => <NavItem key={item.tab} {...item} />)}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        {GROUPS.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => <NavItem key={item.tab} {...item} />)}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
        <SidebarGroup className="min-h-0 flex-1">
          <SidebarGroupContent className="flex min-h-0 flex-1 flex-col">
            <ResourceTree />
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </SidebarRoot>
  );
}
