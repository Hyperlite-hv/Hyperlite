import { useEffect, useState } from "react";
import { useInfraStore } from "../store/useInfraStore";
import Tabs from "../components/Tabs";
import StatusBadge from "../components/StatusBadge";

import DatacenterSummaryTab from "../panels/datacenter/DatacenterSummaryTab";
import ClusterTab from "../panels/datacenter/ClusterTab";
import DcStorageTab from "../panels/datacenter/StorageTab";
import BackupsTab from "../panels/datacenter/BackupsTab";
import PermissionsTab from "../panels/datacenter/PermissionsTab";
import DcFirewallTab from "../panels/datacenter/FirewallTab";

import NodeSummaryTab from "../panels/node/NodeSummaryTab";
import NodeSystemTab from "../panels/node/NodeSystemTab";
import NodeNetworkTab from "../panels/node/NodeNetworkTab";
import NodeDiskTab from "../panels/node/NodeDiskTab";
import NodeFirewallTab from "../panels/node/NodeFirewallTab";
import NodeTasksTab from "../panels/node/NodeTasksTab";

import VMSummaryTab from "../panels/vm/VMSummaryTab";
import VMConsoleTab from "../panels/vm/VMConsoleTab";
import VMHardwareTab from "../panels/vm/VMHardwareTab";
import VMOptionsTab from "../panels/vm/VMOptionsTab";
import VMBackupTab from "../panels/vm/VMBackupTab";
import VMSnapshotsTab from "../panels/vm/VMSnapshotsTab";

const DATACENTER_TABS = [
  { id: "summary", label: "Resume", Component: DatacenterSummaryTab },
  { id: "cluster", label: "Cluster", Component: ClusterTab },
  { id: "storage", label: "Stockage", Component: DcStorageTab },
  { id: "backups", label: "Sauvegardes", Component: BackupsTab },
  { id: "permissions", label: "Permissions", Component: PermissionsTab },
  { id: "firewall", label: "Pare-feu", Component: DcFirewallTab },
];

const NODE_TABS = [
  { id: "summary", label: "Resume", Component: NodeSummaryTab },
  { id: "system", label: "Resume systeme", Component: NodeSystemTab },
  { id: "network", label: "Reseau", Component: NodeNetworkTab },
  { id: "disk", label: "Stockage disque", Component: NodeDiskTab },
  { id: "firewall", label: "Pare-feu", Component: NodeFirewallTab },
  { id: "tasks", label: "Taches", Component: NodeTasksTab },
];

const VM_TABS = [
  { id: "summary", label: "Resume", Component: VMSummaryTab },
  { id: "console", label: "Console", Component: VMConsoleTab },
  { id: "hardware", label: "Materiel", Component: VMHardwareTab },
  { id: "options", label: "Options", Component: VMOptionsTab },
  { id: "backup", label: "Sauvegarde", Component: VMBackupTab },
  { id: "snapshots", label: "Snapshots", Component: VMSnapshotsTab },
];

function titleFor(selection, nodes, vms, containers) {
  if (selection.type === "datacenter") return "Datacenter";
  if (selection.type === "node") return nodes.find((n) => n.id === selection.id)?.nom || selection.id;
  if (selection.type === "vm") return vms.find((v) => v.nom === selection.id)?.nom || selection.id;
  if (selection.type === "container") return containers.find((c) => c.nom === selection.id)?.nom || selection.id;
  return selection.id;
}

export default function CentralPanel() {
  const { selection, nodes, vms, containers } = useInfraStore((s) => ({
    selection: s.selection, nodes: s.nodes, vms: s.vms, containers: s.containers,
  }));
  const [activeTab, setActiveTab] = useState("summary");

  useEffect(() => setActiveTab("summary"), [selection.type, selection.id]);

  if (selection.type === "storage") {
    return (
      <div className="p-4">
        <h2 className="text-lg font-semibold text-anthracite-100">{selection.id}</h2>
        <p className="mt-1 text-sm text-anthracite-300">Selectionnez un node pour voir le detail de ses pools de stockage (onglet "Stockage disque").</p>
      </div>
    );
  }

  const tabSet = selection.type === "node" ? NODE_TABS : (selection.type === "vm" || selection.type === "container") ? VM_TABS : DATACENTER_TABS;
  const resource = selection.type === "vm" ? vms.find((v) => v.nom === selection.id)
    : selection.type === "container" ? containers.find((c) => c.nom === selection.id)
    : selection.type === "node" ? nodes.find((n) => n.id === selection.id)
    : null;
  const ActiveComponent = tabSet.find((t) => t.id === activeTab)?.Component || tabSet[0].Component;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-anthracite-600">
        <h2 className="text-base font-semibold text-anthracite-100">{titleFor(selection, nodes, vms, containers)}</h2>
        {resource?.etat && <StatusBadge etat={resource.etat} />}
        {resource?.alerte && <span className="text-xs text-status-warning">{resource.alerte}</span>}
      </div>
      <Tabs tabs={tabSet} active={activeTab} onChange={setActiveTab} />
      <div className="flex-1 overflow-y-auto p-4">
        <ActiveComponent resource={resource} selection={selection} />
      </div>
    </div>
  );
}
