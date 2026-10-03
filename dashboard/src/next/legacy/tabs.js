// Bridge to the screens that are not rebuilt yet: the same panel components, rendered inside the new
// workspace. Each domain replaces its entries here as it is migrated.
import SecurityPage, { SecuritySsoPage } from "../pages/SecurityPage";
import LibraryPage from "../pages/LibraryPage";
import AutomationPage from "../pages/AutomationPage";
import NodesPage from "../pages/NodesPage";
import HaPage from "../pages/HaPage";
import NotificationsPage from "../pages/NotificationsPage";
import ContainersPage from "../pages/ContainersPage";
import KubernetesPage from "../pages/KubernetesPage";
import CompatibilityPage from "../pages/CompatibilityPage";
import NodeSummary from "../pages/NodeSummary";
import { NodeSystemPage, NodeNetworkPage, NodeDiskPage, NodeShellPage, NodeCompatPage } from "../pages/NodePages";
import VmList from "../pages/VmList";
import Overview from "../pages/Overview";
import ActivityPage from "../pages/ActivityPage";
import StoragePage from "../pages/StoragePage";
import NetworkPage from "../pages/NetworkPage";
import JournalPage from "../pages/JournalPage";
import BackupsPage from "../pages/BackupsPage";
import ExportsPage from "../pages/ExportsPage";
import SnapshotsPage from "../pages/SnapshotsPage";
import VmSummary from "../pages/VmSummary";
import VmConsole from "../pages/VmConsole";
import { VmSnapshotsPage, VmBackupPage } from "../pages/VmSnapshotsBackup";
import { VmHardwarePage, VmOptionsPage, VmNetworkPage } from "../pages/VmConfigure";
import VmPerformancePage, { NodePerformancePage } from "../pages/VmPerformance";
import { ContainerSummary, ContainerConsolePage, ContainerBackupsPage } from "../pages/ContainerPages";
import VmCloudInitPage from "../pages/VmCloudInit";
import VmAdvancedPage from "../pages/VmAdvanced";
import MetricsPage from "../pages/MetricsPage";
import ApiPage from "../pages/ApiPage";
import CfsPage from "../pages/CfsPage";
import { NodeUpdatesPage } from "../pages/NodeHostSystem";
import { VmPermissionsPage, ContainerPermissionsPage } from "../pages/ObjectPermissions";

// Datacenter tabs are grouped by the new sections; every legacy `?tab=` id stays valid.
export const DATACENTER_TABS = {
  summary: Overview, vms: VmList, snapshots: SnapshotsPage, activity: ActivityPage, storage: StoragePage, templates: LibraryPage, library: LibraryPage, backups: BackupsPage,
  exports: ExportsPage, permissions: SecurityPage, reseau: NetworkPage, automation: AutomationPage, containers: ContainersPage, kubernetes: KubernetesPage,
  nodes: NodesPage, ha: HaPage, compat: CompatibilityPage, notifications: NotificationsPage, sso: SecuritySsoPage, journal: JournalPage, metrics: MetricsPage, api: ApiPage, cfs: CfsPage,
};
export const NODE_TABS = {
  summary: NodeSummary, perf: NodePerformancePage, system: NodeSystemPage, updates: NodeUpdatesPage, network: NodeNetworkPage, disk: NodeDiskPage, tasks: ActivityPage, compat: NodeCompatPage, shell: NodeShellPage,
};
export const VM_TABS = {
  summary: VmSummary, perf: VmPerformancePage, console: VmConsole, hardware: VmHardwarePage, options: VmOptionsPage, network: VmNetworkPage,
  backup: VmBackupPage, snapshots: VmSnapshotsPage, tasks: ActivityPage, cloudinit: VmCloudInitPage, advanced: VmAdvancedPage, permissions: VmPermissionsPage,
};
export const CONTAINER_TABS = { summary: ContainerSummary, console: ContainerConsolePage, backup: ContainerBackupsPage, tasks: ActivityPage, permissions: ContainerPermissionsPage };


// vSphere-style model: every inventory object has a few top tabs; a top tab that holds several
// pages shows them as a vertical menu on its left. Page ids are the historical `?tab=` ids, so
// every existing link keeps working.
const page = (id, group, label) => ({ page: id, group, label });
export const OBJECT_TABS = {
  // Datacenter pages are reached from the sidebar: every page id resolves to itself.
  datacenter: Object.keys(DATACENTER_TABS).map((id) => ({ id, label: `tab.${id}`, pages: [page(id)] })),
  // Flat tabs, like the VM page: every page is one click away.
  node: [
    { id: "summary", label: "tab.summary", pages: [page("summary")] },
    { id: "perf", label: "tab.perf", pages: [page("perf")] },
    { id: "system", label: "tab.system", pages: [page("system")] },
    { id: "updates", label: "tab.updates", pages: [page("updates")], admin: true },
    { id: "network", label: "tab.network", pages: [page("network")] },
    { id: "disk", label: "tab.disk", pages: [page("disk")] },
    { id: "tasks", label: "tab.tasks", pages: [page("tasks")] },
    { id: "compat", label: "tab.compat", pages: [page("compat")] },
    { id: "shell", label: "tab.shell", pages: [page("shell")] },
  ],
  // Same order as the reference console; Hardware keeps Options (resources and live limits) as its second page.
  vm: [
    { id: "summary", label: "tab.summary", pages: [page("summary")] },
    { id: "perf", label: "tab.perf", pages: [page("perf")] },
    { id: "snapshots", label: "tab.snapshots", pages: [page("snapshots")] },
    { id: "backup", label: "tab.backups", pages: [page("backup")] },
    { id: "hardware", label: "tab.hardware", pages: [page("hardware"), page("advanced"), page("options"), page("cloudinit")] },
    { id: "network", label: "tab.network", pages: [page("network")] },
    { id: "console", label: "tab.console", pages: [page("console")] },
    { id: "tasks", label: "tab.tasks", pages: [page("tasks")] },
    { id: "permissions", label: "tab.objPermissions", pages: [page("permissions")], admin: true },
  ],
  container: [
    { id: "summary", label: "tab.summary", pages: [page("summary")] },
    { id: "console", label: "tab.console", pages: [page("console")] },
    { id: "backup", label: "tab.backups", pages: [page("backup")] },
    { id: "tasks", label: "tab.tasks", pages: [page("tasks")] },
    { id: "permissions", label: "tab.objPermissions", pages: [page("permissions")], admin: true },
  ],
};

// Resolves the top tab that owns a page id (unknown ids fall back to the first tab).
export function locate(type, pageId) {
  const tabs = OBJECT_TABS[type] || OBJECT_TABS.datacenter;
  const top = tabs.find((t) => t.pages.some((p) => p.page === pageId)) || tabs[0];
  const found = top.pages.find((p) => p.page === pageId) || top.pages[0];
  return { tabs, top, page: found.page };
}
