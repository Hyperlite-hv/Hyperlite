// Bridge to the screens that are not rebuilt yet: the same panel components, rendered inside the new
// workspace. Each domain replaces its entries here as it is migrated (see docs/frontend-rebuild/07).
import DcStorageTab from "../../panels/datacenter/StorageTab";
import BackupsTab from "../../panels/datacenter/BackupsTab";
import PermissionsTab from "../../panels/datacenter/PermissionsTab";
import TemplatesTab from "../../panels/datacenter/TemplatesTab";
import NetworkOverviewTab from "../../panels/datacenter/NetworkOverviewTab";
import AutomationTab from "../../panels/datacenter/AutomationTab";
import NodesTab from "../../panels/datacenter/NodesTab";
import HaTab from "../../panels/datacenter/HaTab";
import NotificationsTab from "../../panels/datacenter/NotificationsTab";
import SSOTab from "../../panels/datacenter/SSOTab";
import ContainersTab from "../../panels/datacenter/ContainersTab";
import JournalTab from "../../panels/datacenter/JournalTab";
import ExportsTab from "../../panels/datacenter/ExportsTab";
import ActivityTab from "../../panels/datacenter/ActivityTab";
import CompatibilityTab from "../../panels/datacenter/CompatibilityTab";
import NodeSummary from "../pages/NodeSummary";
import VmList from "../pages/VmList";
import Overview from "../pages/Overview";
import VmSummary from "../pages/VmSummary";
import NodeSystemTab from "../../panels/node/NodeSystemTab";
import NodeNetworkTab from "../../panels/node/NodeNetworkTab";
import NodeDiskTab from "../../panels/node/NodeDiskTab";
import NodeTasksTab from "../../panels/node/NodeTasksTab";
import NodeShellTab from "../../panels/node/NodeShellTab";
import NodeCompatibilityTab from "../../panels/node/NodeCompatibilityTab";
import VMConsoleTab from "../../panels/vm/VMConsoleTab";
import VMHardwareTab from "../../panels/vm/VMHardwareTab";
import VMOptionsTab from "../../panels/vm/VMOptionsTab";
import VMBackupTab from "../../panels/vm/VMBackupTab";
import VMSnapshotsTab from "../../panels/vm/VMSnapshotsTab";

// Datacenter tabs are grouped by the new sections; every legacy `?tab=` id stays valid.
export const DATACENTER_TABS = {
  summary: Overview, vms: VmList, activity: ActivityTab, storage: DcStorageTab, templates: TemplatesTab, backups: BackupsTab,
  exports: ExportsTab, permissions: PermissionsTab, reseau: NetworkOverviewTab, automation: AutomationTab, containers: ContainersTab,
  nodes: NodesTab, ha: HaTab, compat: CompatibilityTab, notifications: NotificationsTab, sso: SSOTab, journal: JournalTab,
};
export const NODE_TABS = {
  summary: NodeSummary, system: NodeSystemTab, network: NodeNetworkTab, disk: NodeDiskTab, tasks: NodeTasksTab, compat: NodeCompatibilityTab, shell: NodeShellTab,
};
export const VM_TABS = {
  summary: VmSummary, console: VMConsoleTab, hardware: VMHardwareTab, options: VMOptionsTab, backup: VMBackupTab, snapshots: VMSnapshotsTab,
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
