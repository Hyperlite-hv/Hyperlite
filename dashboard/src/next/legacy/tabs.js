// The screens of the console, each loaded when it is first opened: one bundle with every page made the first screen
// wait for all of them. A file that cannot be fetched reloads the page (lib/lazyRetry.js).
import { lazy } from "react";
import { loadOrReload } from "../../lib/lazyRetry";

const onDemand = (load, name = "default") => lazy(() => loadOrReload(load).then((m) => ({ default: m[name] })));

const SecurityPage = onDemand(() => import("../pages/SecurityPage"));
const SecuritySsoPage = onDemand(() => import("../pages/SecurityPage"), "SecuritySsoPage");
const LibraryPage = onDemand(() => import("../pages/LibraryPage"));
const AutomationPage = onDemand(() => import("../pages/AutomationPage"));
const NodesPage = onDemand(() => import("../pages/NodesPage"));
const HaPage = onDemand(() => import("../pages/HaPage"));
const DrsPage = onDemand(() => import("../pages/DrsPage"));
const NotificationsPage = onDemand(() => import("../pages/NotificationsPage"));
const ContainersPage = onDemand(() => import("../pages/ContainersPage"));
const KubernetesPage = onDemand(() => import("../pages/KubernetesPage"));
const CompatibilityPage = onDemand(() => import("../pages/CompatibilityPage"));
const NodeSummary = onDemand(() => import("../pages/NodeSummary"));
const NodeSystemPage = onDemand(() => import("../pages/NodePages"), "NodeSystemPage");
const NodeNetworkPage = onDemand(() => import("../pages/NodePages"), "NodeNetworkPage");
const NodeDiskPage = onDemand(() => import("../pages/NodePages"), "NodeDiskPage");
const NodeShellPage = onDemand(() => import("../pages/NodePages"), "NodeShellPage");
const NodeCompatPage = onDemand(() => import("../pages/NodePages"), "NodeCompatPage");
const VmList = onDemand(() => import("../pages/VmList"));
const ClusterPerf = onDemand(() => import("../pages/Overview"), "ClusterPerf");
const Home = onDemand(() => import("../pages/Home"));
const ServicesPage = onDemand(() => import("../pages/ServicesPage"));
const AllResourcesPage = onDemand(() => import("../pages/ServicesPage"), "AllResourcesPage");
const CreatePage = onDemand(() => import("../pages/ServicesPage"), "CreatePage");
const ActivityPage = onDemand(() => import("../pages/ActivityPage"));
const StoragePage = onDemand(() => import("../pages/StoragePage"));
const NetworkPage = onDemand(() => import("../pages/NetworkPage"));
const FirewallPage = onDemand(() => import("../pages/FirewallPage"));
const ActivityTasksPage = onDemand(() => import("../pages/ActivityHub"), "ActivityTasksPage");
const ActivityJournalPage = onDemand(() => import("../pages/ActivityHub"), "ActivityJournalPage");
const BackupsPage = onDemand(() => import("../pages/BackupsPage"));
const ExportsPage = onDemand(() => import("../pages/ExportsPage"));
const SnapshotsPage = onDemand(() => import("../pages/SnapshotsPage"));
const VmSummary = onDemand(() => import("../pages/VmSummary"));
const VmConsole = onDemand(() => import("../pages/VmConsole"));
const VmSnapshotsPage = onDemand(() => import("../pages/VmSnapshotsBackup"), "VmSnapshotsPage");
const VmBackupPage = onDemand(() => import("../pages/VmSnapshotsBackup"), "VmBackupPage");
const VmHardwarePage = onDemand(() => import("../pages/VmConfigure"), "VmHardwarePage");
const VmOptionsPage = onDemand(() => import("../pages/VmConfigure"), "VmOptionsPage");
const VmNetworkPage = onDemand(() => import("../pages/VmConfigure"), "VmNetworkPage");
const VmPerformancePage = onDemand(() => import("../pages/VmPerformance"));
const NodePerformancePage = onDemand(() => import("../pages/VmPerformance"), "NodePerformancePage");
const ContainerSummary = onDemand(() => import("../pages/ContainerPages"), "ContainerSummary");
const ContainerConsolePage = onDemand(() => import("../pages/ContainerPages"), "ContainerConsolePage");
const ContainerBackupsPage = onDemand(() => import("../pages/ContainerPages"), "ContainerBackupsPage");
const ContainerSnapshotsPage = onDemand(() => import("../pages/ContainerPages"), "ContainerSnapshotsPage");
const VmCloudInitPage = onDemand(() => import("../pages/VmCloudInit"));
const VmAdvancedPage = onDemand(() => import("../pages/VmAdvanced"));
const MetricsPage = onDemand(() => import("../pages/MetricsPage"));
const ApiPage = onDemand(() => import("../pages/ApiPage"));
const CfsPage = onDemand(() => import("../pages/CfsPage"));
const SitesPage = onDemand(() => import("../pages/SitesPage"));
const NodeUpdatesPage = onDemand(() => import("../pages/NodeHostSystem"), "NodeUpdatesPage");
const VmPermissionsPage = onDemand(() => import("../pages/ObjectPermissions"), "VmPermissionsPage");
const ContainerPermissionsPage = onDemand(() => import("../pages/ObjectPermissions"), "ContainerPermissionsPage");
const AdvisorPage = onDemand(() => import("../pages/AdvisorPage"));
const VmAdvisorPage = onDemand(() => import("../pages/AdvisorPage"), "VmAdvisorPage");
const NodeAdvisorPage = onDemand(() => import("../pages/AdvisorPage"), "NodeAdvisorPage");

// The datacenter pages that draw their own header (the others get one from the workspace). Kept here because a page
// that is not loaded yet cannot say so itself; src/__tests__/ownHeader.test.js checks it against the pages.
export const OWN_HEADER = new Set(["activity", "advisor", "all", "api", "automation", "backups", "cfs", "compat", "containers", "create", "drs", "exports", "ha", "journal", "kubernetes", "library", "network", "nodes", "notifications", "perf", "permissions", "services", "sites", "snapshots", "sso", "storage", "summary", "templates", "vms"]);

// Datacenter tabs are grouped by the new sections; every legacy `?tab=` id stays valid.
export const DATACENTER_TABS = {
  summary: Home, services: ServicesPage, all: AllResourcesPage, create: CreatePage, perf: ClusterPerf, vms: VmList, snapshots: SnapshotsPage, activity: ActivityTasksPage, storage: StoragePage, templates: LibraryPage, library: LibraryPage, backups: BackupsPage,
  exports: ExportsPage, permissions: SecurityPage, network: NetworkPage, firewall: FirewallPage, automation: AutomationPage, containers: ContainersPage, kubernetes: KubernetesPage,
  nodes: NodesPage, ha: HaPage, drs: DrsPage, compat: CompatibilityPage, notifications: NotificationsPage, sso: SecuritySsoPage, journal: ActivityJournalPage, metrics: MetricsPage, api: ApiPage, cfs: CfsPage, sites: SitesPage, advisor: AdvisorPage,
};
export const NODE_TABS = {
  summary: NodeSummary, perf: NodePerformancePage, system: NodeSystemPage, updates: NodeUpdatesPage, network: NodeNetworkPage, disk: NodeDiskPage, tasks: ActivityPage, compat: NodeCompatPage, shell: NodeShellPage, advisor: NodeAdvisorPage,
};
export const VM_TABS = {
  summary: VmSummary, perf: VmPerformancePage, console: VmConsole, hardware: VmHardwarePage, options: VmOptionsPage, network: VmNetworkPage,
  backup: VmBackupPage, snapshots: VmSnapshotsPage, tasks: ActivityPage, cloudinit: VmCloudInitPage, advanced: VmAdvancedPage, permissions: VmPermissionsPage, advisor: VmAdvisorPage,
};
export const CONTAINER_TABS = { summary: ContainerSummary, console: ContainerConsolePage, snapshots: ContainerSnapshotsPage, backup: ContainerBackupsPage, tasks: ActivityPage, permissions: ContainerPermissionsPage };


// Inventory model: every inventory object has a few top tabs; a top tab that holds several
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
    { id: "advisor", label: "tab.advisor", pages: [page("advisor")] },
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
    { id: "advisor", label: "tab.advisor", pages: [page("advisor")] },
  ],
  container: [
    { id: "summary", label: "tab.summary", pages: [page("summary")] },
    { id: "console", label: "tab.console", pages: [page("console")] },
    { id: "snapshots", label: "tab.snapshots", pages: [page("snapshots")] },
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
