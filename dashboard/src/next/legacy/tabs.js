// Bridge to the screens that are not rebuilt yet: the same panel components, rendered inside the new
// workspace. Each domain replaces its entries here as it is migrated (see docs/frontend-rebuild/07).
import SecurityPage from "../pages/SecurityPage";
import TemplatesPage from "../pages/TemplatesPage";
import AutomationPage from "../pages/AutomationPage";
import NodesPage from "../pages/NodesPage";
import HaPage from "../pages/HaPage";
import NotificationsPage from "../pages/NotificationsPage";
import SsoPage from "../pages/SsoPage";
import ContainersPage from "../pages/ContainersPage";
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
import VMHardwareTab from "../../panels/vm/VMHardwareTab";
import VMOptionsTab from "../../panels/vm/VMOptionsTab";
import VMBackupTab from "../../panels/vm/VMBackupTab";
import VMSnapshotsTab from "../../panels/vm/VMSnapshotsTab";

// Datacenter tabs are grouped by the new sections; every legacy `?tab=` id stays valid.
export const DATACENTER_TABS = {
  summary: Overview, vms: VmList, snapshots: SnapshotsPage, activity: ActivityPage, storage: StoragePage, templates: TemplatesPage, backups: BackupsPage,
  exports: ExportsPage, permissions: SecurityPage, reseau: NetworkPage, automation: AutomationPage, containers: ContainersPage,
  nodes: NodesPage, ha: HaPage, compat: CompatibilityPage, notifications: NotificationsPage, sso: SsoPage, journal: JournalPage,
};
export const NODE_TABS = {
  summary: NodeSummary, system: NodeSystemPage, network: NodeNetworkPage, disk: NodeDiskPage, tasks: ActivityPage, compat: NodeCompatPage, shell: NodeShellPage,
};
export const VM_TABS = {
  summary: VmSummary, console: VmConsole, hardware: VMHardwareTab, options: VMOptionsTab, backup: VMBackupTab, snapshots: VMSnapshotsTab,
};


// vSphere-style model: every inventory object has a few top tabs; a top tab that holds several
// pages shows them as a vertical menu on its left. Page ids are the historical `?tab=` ids, so
// every existing link keeps working.
const page = (id, group, label) => ({ page: id, group, label });
export const OBJECT_TABS = {
  datacenter: [
    { id: "summary", label: "tab.summary", pages: [page("summary")] },
    { id: "monitor", label: "tab.monitor", pages: [page("activity", "group.monitor"), page("journal")] },
    { id: "configure", label: "tab.configure", pages: [
      page("nodes", "group.cluster"), page("ha"), page("compat"),
      page("storage", "group.resources"), page("reseau"), page("templates"),
      page("backups", "group.protection"), page("exports"),
      page("automation", "group.services"), page("notifications"),
    ] },
    { id: "permissions", label: "tab.permissions", pages: [page("permissions", "group.access", "tab.permissions.page"), page("sso")] },
    { id: "containers", label: "tab.containers", pages: [page("containers")] },
    { id: "vms", label: "tab.vms", pages: [page("vms")] },
    { id: "snapshots", label: "tab.snapshots", pages: [page("snapshots")] },
  ],
  node: [
    { id: "summary", label: "tab.summary", pages: [page("summary")] },
    { id: "monitor", label: "tab.monitor", pages: [page("system", "group.monitor"), page("tasks")] },
    { id: "configure", label: "tab.configure", pages: [page("network", "group.resources"), page("disk"), page("compat", "group.cluster"), page("shell", "group.platform")] },
  ],
  vm: [
    { id: "summary", label: "tab.summary", pages: [page("summary")] },
    { id: "console", label: "tab.console", pages: [page("console")] },
    { id: "configure", label: "tab.configure", pages: [page("hardware", "group.resources"), page("options")] },
    { id: "snapshots", label: "tab.snapshots", pages: [page("snapshots")] },
    { id: "backup", label: "tab.backup", pages: [page("backup")] },
  ],
};

// Resolves the top tab that owns a page id (unknown ids fall back to the first tab).
export function locate(type, pageId) {
  const tabs = OBJECT_TABS[type] || OBJECT_TABS.datacenter;
  const top = tabs.find((t) => t.pages.some((p) => p.page === pageId)) || tabs[0];
  const found = top.pages.find((p) => p.page === pageId) || top.pages[0];
  return { tabs, top, page: found.page };
}
