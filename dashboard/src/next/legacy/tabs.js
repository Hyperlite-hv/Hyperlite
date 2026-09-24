// Bridge to the screens that are not rebuilt yet: the same panel components, rendered inside the new
// workspace. Each domain replaces its entries here as it is migrated (see docs/frontend-rebuild/07).
import DatacenterSummaryTab from "../../panels/datacenter/DatacenterSummaryTab";
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
import NodeSummaryTab from "../../panels/node/NodeSummaryTab";
import NodeSystemTab from "../../panels/node/NodeSystemTab";
import NodeNetworkTab from "../../panels/node/NodeNetworkTab";
import NodeDiskTab from "../../panels/node/NodeDiskTab";
import NodeTasksTab from "../../panels/node/NodeTasksTab";
import NodeShellTab from "../../panels/node/NodeShellTab";
import NodeCompatibilityTab from "../../panels/node/NodeCompatibilityTab";
import VMSummaryTab from "../../panels/vm/VMSummaryTab";
import VMConsoleTab from "../../panels/vm/VMConsoleTab";
import VMHardwareTab from "../../panels/vm/VMHardwareTab";
import VMOptionsTab from "../../panels/vm/VMOptionsTab";
import VMBackupTab from "../../panels/vm/VMBackupTab";
import VMSnapshotsTab from "../../panels/vm/VMSnapshotsTab";

// Datacenter tabs are grouped by the new sections; every legacy `?tab=` id stays valid.
export const DATACENTER_TABS = {
  summary: DatacenterSummaryTab, activity: ActivityTab, storage: DcStorageTab, templates: TemplatesTab, backups: BackupsTab,
  exports: ExportsTab, permissions: PermissionsTab, reseau: NetworkOverviewTab, automation: AutomationTab, containers: ContainersTab,
  nodes: NodesTab, ha: HaTab, compat: CompatibilityTab, notifications: NotificationsTab, sso: SSOTab, journal: JournalTab,
};
export const SECTIONS = {
  overview: ["summary"],
  infrastructure: ["nodes", "ha", "compat", "storage", "reseau", "templates", "backups", "exports"],
  vms: ["containers"],
  activity: ["activity", "journal"],
  security: ["permissions", "sso"],
  settings: ["automation", "notifications"],
};
export const NODE_TABS = {
  summary: NodeSummaryTab, system: NodeSystemTab, network: NodeNetworkTab, disk: NodeDiskTab, tasks: NodeTasksTab, compat: NodeCompatibilityTab, shell: NodeShellTab,
};
export const VM_TABS = {
  summary: VMSummaryTab, console: VMConsoleTab, hardware: VMHardwareTab, options: VMOptionsTab, backup: VMBackupTab, snapshots: VMSnapshotsTab,
};

export function sectionOfTab(tab) {
  return Object.keys(SECTIONS).find((s) => SECTIONS[s].includes(tab)) || "overview";
}
