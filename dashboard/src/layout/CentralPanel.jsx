import { useEffect } from "react";
import { useInfraStore } from "../store/useInfraStore";
import Tabs from "../components/Tabs";
import StatusBadge from "../components/StatusBadge";

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
import ContainersTab from "../panels/datacenter/ContainersTab";
import JournalTab from "../panels/datacenter/JournalTab";
import ExportsTab from "../panels/datacenter/ExportsTab";
import ActivityTab from "../panels/datacenter/ActivityTab";

import NodeSummaryTab from "../panels/node/NodeSummaryTab";
import NodeSystemTab from "../panels/node/NodeSystemTab";
import NodeNetworkTab from "../panels/node/NodeNetworkTab";
import NodeDiskTab from "../panels/node/NodeDiskTab";
import NodeTasksTab from "../panels/node/NodeTasksTab";
import NodeShellTab from "../panels/node/NodeShellTab";

import VMSummaryTab from "../panels/vm/VMSummaryTab";
import VMConsoleTab from "../panels/vm/VMConsoleTab";
import VMHardwareTab from "../panels/vm/VMHardwareTab";
import VMOptionsTab from "../panels/vm/VMOptionsTab";
import VMBackupTab from "../panels/vm/VMBackupTab";
import VMSnapshotsTab from "../panels/vm/VMSnapshotsTab";

const DATACENTER_TABS = [
  { id: "summary", label: "Résumé", Component: DatacenterSummaryTab },
  { id: "activity", label: "Activité récente", Component: ActivityTab },
  { id: "storage", label: "Stockage", Component: DcStorageTab },
  { id: "templates", label: "Templates", Component: TemplatesTab },
  { id: "backups", label: "Sauvegardes", Component: BackupsTab },
  { id: "exports", label: "Exports", Component: ExportsTab },
  { id: "permissions", label: "Permissions", Component: PermissionsTab },
  { id: "reseau", label: "Réseau", Component: NetworkOverviewTab },
  { id: "automation", label: "Automation", Component: AutomationTab },
  { id: "containers", label: "Conteneurs", Component: ContainersTab },
  { id: "nodes", label: "Nœuds", Component: NodesTab },
  { id: "ha", label: "HA", Component: HaTab },
  { id: "notifications", label: "Notifications", Component: NotificationsTab },
  { id: "journal", label: "Journal", Component: JournalTab },
];

const NODE_TABS = [
  { id: "summary", label: "Résumé", Component: NodeSummaryTab },
  { id: "system", label: "Résumé système", Component: NodeSystemTab },
  { id: "network", label: "Réseau", Component: NodeNetworkTab },
  { id: "disk", label: "Stockage disque", Component: NodeDiskTab },
  { id: "tasks", label: "Tâches", Component: NodeTasksTab },
  { id: "shell", label: "Shell", Component: NodeShellTab },
];

const VM_TABS = [
  { id: "summary", label: "Résumé", Component: VMSummaryTab },
  { id: "console", label: "Console", Component: VMConsoleTab },
  { id: "hardware", label: "Matériel", Component: VMHardwareTab },
  { id: "options", label: "Options", Component: VMOptionsTab },
  { id: "backup", label: "Sauvegarde", Component: VMBackupTab },
  { id: "snapshots", label: "Snapshots", Component: VMSnapshotsTab },
];

function titleFor(selection, nodes, vms) {
  if (selection.type === "datacenter") return "Datacenter";
  if (selection.type === "node") return nodes.find((n) => n.id === selection.id)?.nom || selection.id;
  if (selection.type === "vm") return vms.find((v) => v.nom === selection.id)?.nom || selection.id;
  return selection.id;
}

export default function CentralPanel() {
  const { selection, nodes, vms, pendingTab, clearPendingTab, activeTab, setActiveTab } = useInfraStore((s) => ({
    selection: s.selection, nodes: s.nodes, vms: s.vms,
    pendingTab: s.pendingTab, clearPendingTab: s.clearPendingTab,
    activeTab: s.activeTab, setActiveTab: s.setActiveTab,
  }));

  // Deux effets distincts, pas un seul -- BUG REEL trouve le 2026-09-17 en
  // testant sur un vrai navigateur (Antho : "les boutons sur le côté
  // gauche marche pas") : avec un seul useEffect deps=[selection.type,
  // selection.id], cliquer un item de SidebarRail alors qu'on est deja sur
  // la vue "datacenter" (le cas le plus courant : selection.type/id ne
  // changent pas, seul pendingTab change) ne re-declenchait JAMAIS l'effet
  // -- pendingTab restait pose dans le store mais n'etait jamais consomme,
  // l'onglet affiche ne changeait pas. Corrige en separant : l'effet
  // "nouvelle selection -> revenir a Résumé" ne depend QUE de la selection
  // (comportement inchange), l'effet "onglet demande" ne depend QUE de
  // pendingTab et se declenche donc bien a chaque navigateTo(), meme sans
  // changement de selection. L'ordre de declaration importe : celui-ci
  // s'execute apres, donc gagne si les deux changent en meme temps (cas
  // navigateTo(nouvelleSelection, onglet), ex. clic sur une ligne de la
  // table des nœuds).
  useEffect(() => {
    setActiveTab("summary");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.type, selection.id]);

  useEffect(() => {
    if (pendingTab) {
      setActiveTab(pendingTab);
      clearPendingTab();
    }
  }, [pendingTab, setActiveTab, clearPendingTab]);

  if (selection.type === "storage") {
    return (
      <div className="p-4">
        <h2 className="text-lg font-semibold text-anthracite-100">{selection.id}</h2>
        <p className="mt-1 text-sm text-anthracite-300">Sélectionnez un nœud pour voir le détail de ses pools de stockage (onglet "Stockage disque").</p>
      </div>
    );
  }

  const tabSet = selection.type === "node" ? NODE_TABS : selection.type === "vm" ? VM_TABS : DATACENTER_TABS;
  const resource = selection.type === "vm" ? vms.find((v) => v.nom === selection.id)
    : selection.type === "node" ? nodes.find((n) => n.id === selection.id)
    : null;
  const ActiveComponent = tabSet.find((t) => t.id === activeTab)?.Component || tabSet[0].Component;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-anthracite-600">
        <h2 className="text-base font-semibold text-anthracite-100">{titleFor(selection, nodes, vms)}</h2>
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
