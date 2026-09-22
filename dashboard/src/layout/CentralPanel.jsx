import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../store/useInfraStore";
import StatusBadge from "../components/StatusBadge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

import DatacenterSummaryTab from "../panels/datacenter/DatacenterSummaryTab";
import DcStorageTab from "../panels/datacenter/StorageTab";
import BackupsTab from "../panels/datacenter/BackupsTab";
import PermissionsTab from "../panels/datacenter/PermissionsTab";
import TemplatesTab from "../panels/datacenter/TemplatesTab";
import NetworkOverviewTab from "../panels/datacenter/NetworkOverviewTab";
import AutomationTab from "../panels/datacenter/AutomationTab";
import NodesTab from "../panels/datacenter/NodesTab";
import HaTab from "../panels/datacenter/HaTab";
import NotificationsTab from "../panels/datacenter/NotificationsTab";
import SSOTab from "../panels/datacenter/SSOTab";
import ContainersTab from "../panels/datacenter/ContainersTab";
import JournalTab from "../panels/datacenter/JournalTab";
import ExportsTab from "../panels/datacenter/ExportsTab";
import ActivityTab from "../panels/datacenter/ActivityTab";
import CompatibilityTab from "../panels/datacenter/CompatibilityTab";

import NodeSummaryTab from "../panels/node/NodeSummaryTab";
import NodeSystemTab from "../panels/node/NodeSystemTab";
import NodeNetworkTab from "../panels/node/NodeNetworkTab";
import NodeDiskTab from "../panels/node/NodeDiskTab";
import NodeTasksTab from "../panels/node/NodeTasksTab";
import NodeShellTab from "../panels/node/NodeShellTab";
import NodeCompatibilityTab from "../panels/node/NodeCompatibilityTab";

import VMSummaryTab from "../panels/vm/VMSummaryTab";
import VMConsoleTab from "../panels/vm/VMConsoleTab";
import VMHardwareTab from "../panels/vm/VMHardwareTab";
import VMOptionsTab from "../panels/vm/VMOptionsTab";
import VMBackupTab from "../panels/vm/VMBackupTab";
import VMSnapshotsTab from "../panels/vm/VMSnapshotsTab";

const DATACENTER_TABS = [
  { id: "summary", label: "Summary", Component: DatacenterSummaryTab },
  { id: "activity", label: "Recent activity", Component: ActivityTab },
  { id: "storage", label: "Storage", Component: DcStorageTab },
  { id: "templates", label: "Templates", Component: TemplatesTab },
  { id: "backups", label: "Backups", Component: BackupsTab },
  { id: "exports", label: "Exports", Component: ExportsTab },
  { id: "permissions", label: "Permissions", Component: PermissionsTab },
  { id: "reseau", label: "Network", Component: NetworkOverviewTab },
  { id: "automation", label: "Automation", Component: AutomationTab },
  { id: "containers", label: "Containers", Component: ContainersTab },
  { id: "nodes", label: "Nodes", Component: NodesTab },
  { id: "ha", label: "HA", Component: HaTab },
  { id: "compat", label: "Compatibility", Component: CompatibilityTab },
  { id: "notifications", label: "Notifications", Component: NotificationsTab },
  { id: "sso", label: "SSO", Component: SSOTab },
  { id: "journal", label: "Journal", Component: JournalTab },
];

const NODE_TABS = [
  { id: "summary", label: "Summary", Component: NodeSummaryTab },
  { id: "system", label: "System summary", Component: NodeSystemTab },
  { id: "network", label: "Network", Component: NodeNetworkTab },
  { id: "disk", label: "Disk storage", Component: NodeDiskTab },
  { id: "tasks", label: "Tasks", Component: NodeTasksTab },
  { id: "compat", label: "Compatibility", Component: NodeCompatibilityTab },
  { id: "shell", label: "Shell", Component: NodeShellTab },
];

const VM_TABS = [
  { id: "summary", label: "Summary", Component: VMSummaryTab },
  { id: "console", label: "Console", Component: VMConsoleTab },
  { id: "hardware", label: "Hardware", Component: VMHardwareTab },
  { id: "options", label: "Options", Component: VMOptionsTab },
  { id: "backup", label: "Backup", Component: VMBackupTab },
  { id: "snapshots", label: "Snapshots", Component: VMSnapshotsTab },
];

function titleFor(selection, nodes, vms) {
  if (selection.type === "datacenter") return "Datacenter";
  if (selection.type === "node") return nodes.find((n) => n.id === selection.id)?.nom || selection.id;
  if (selection.type === "vm") return vms.find((v) => v.nom === selection.id)?.nom || selection.id;
  return selection.id;
}

export default function CentralPanel() {
  const { selection, nodes, vms, pendingTab, clearPendingTab, activeTab, setActiveTab } = useInfraStore(useShallow((s) => ({
    selection: s.selection, nodes: s.nodes, vms: s.vms,
    pendingTab: s.pendingTab, clearPendingTab: s.clearPendingTab,
    activeTab: s.activeTab, setActiveTab: s.setActiveTab,
  })));

  // Two distinct effects, not a single one. With one useEffect having deps
  // [selection.type, selection.id], clicking a SidebarRail item while already on the
  // "datacenter" view (the most common case: selection.type/id do not change, only
  // pendingTab does) never re-triggered the effect: pendingTab stayed set in the
  // store but was never consumed, so the displayed tab did not change. Fixed by
  // splitting: the "new selection -> back to Summary" effect depends ONLY on the
  // selection (behaviour unchanged), the "requested tab" effect depends ONLY on
  // pendingTab and therefore fires on every navigateTo(), even without a selection
  // change. Declaration order matters: this one runs after, so it wins if both
  // change at the same time (the navigateTo(newSelection, tab) case, e.g. a click
  // on a row of the nodes table).
  // The active tab is mirrored in the URL (?tab=...) so that a reload or a shared link
  // lands on the same tab. The value read at load time is applied as soon as the
  // selection it belongs to is known.
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = useRef(searchParams.get("tab"));

  useEffect(() => {
    const ids = (selection.type === "node" ? NODE_TABS : selection.type === "vm" ? VM_TABS : DATACENTER_TABS).map((t) => t.id);
    const wanted = urlTab.current;
    if (wanted && ids.includes(wanted)) {
      setActiveTab(wanted);
      urlTab.current = null;
      return;
    }
    if (selection.type !== "datacenter") urlTab.current = null;
    setActiveTab("summary");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.type, selection.id]);

  const { pathname } = useLocation();
  useEffect(() => {
    if (urlTab.current) return;
    // Do not touch the URL while it is still being synchronized with a new selection:
    // this navigation would otherwise overwrite the one that changes the path.
    const expectedPath = selection.type === "datacenter" ? "/datacenter" : `/${selection.type}/${encodeURIComponent(selection.id)}`;
    if (pathname !== expectedPath) return;
    const wanted = activeTab === "summary" ? null : activeTab;
    if ((searchParams.get("tab") || null) === wanted) return;
    const next = new URLSearchParams(searchParams);
    if (wanted) next.set("tab", wanted);
    else next.delete("tab");
    setSearchParams(next, { replace: true });
  }, [activeTab, searchParams, setSearchParams, pathname, selection.type, selection.id]);

  useEffect(() => {
    if (pendingTab) {
      setActiveTab(pendingTab);
      clearPendingTab();
    }
  }, [pendingTab, setActiveTab, clearPendingTab]);

  if (selection.type === "storage") {
    return (
      <div className="p-4">
        <h2 className="text-lg font-semibold text-foreground">{selection.id}</h2>
        <p className="mt-1 text-sm text-muted-foreground">Select a node to see the details of its storage pools ("Disk storage" tab).</p>
      </div>
    );
  }

  // The Datacenter level's tab strip was removed here on purpose: every one of
  // its tabs is already reachable from the sidebar rail (Sidebar.jsx), and
  // showing both was a duplicated navigation path (flagged in the UI audit).
  // Node/VM levels keep a tab strip since the rail has no equivalent for them.
  const isDatacenter = selection.type === "datacenter";
  const tabSet = selection.type === "node" ? NODE_TABS : selection.type === "vm" ? VM_TABS : DATACENTER_TABS;
  const resource = selection.type === "vm" ? vms.find((v) => v.nom === selection.id)
    : selection.type === "node" ? nodes.find((n) => n.id === selection.id)
    : null;
  const ActiveComponent = tabSet.find((t) => t.id === activeTab)?.Component || tabSet[0].Component;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-border">
        <h2 className="text-base font-semibold text-foreground">{titleFor(selection, nodes, vms)}</h2>
        {resource?.etat && <StatusBadge etat={resource.etat} />}
        {resource?.alerte && <span className="text-xs text-status-warning">{resource.alerte}</span>}
      </div>
      {!isDatacenter && (
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList variant="line" className="px-4 border-b border-border w-full justify-start overflow-x-auto">
            {tabSet.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>{tab.label}</TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      )}
      <div className="flex-1 overflow-y-auto p-4">
        <ActiveComponent resource={resource} selection={selection} />
      </div>
    </div>
  );
}
