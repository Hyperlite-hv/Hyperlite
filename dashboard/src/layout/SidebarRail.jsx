import {
  LayoutDashboard, Activity, Box, HardDrive, Network, Layers,
  CalendarClock, PackageOpen, ShieldCheck, Workflow, Server, ScrollText,
} from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

// Rail de navigation façon Proxmox VE (capture fournie par Antho,
// 2026-09-13) : une entree par section reellement disponible cote
// Hyperlite (calquee sur DATACENTER_TABS, CentralPanel.jsx) plutot que la
// liste complete de Proxmox (pas de Certificats/Abonnement/Repartition de
// charge/Replication... qui n'existent pas ici, demande explicite "je veut
// juste les fonctionnalités disponibles"). Purement additif : ne remplace
// pas l'arbre Datacenter > Nœud > VM (ResourceTree, juste a droite) qui
// reste la navigation/les raccourcis VM existants -- ce rail ne fait que
// sauter directement a un onglet Datacenter donne sans passer par l'arbre.
const ITEMS = [
  { tab: "summary", label: "Tableau de bord", Icon: LayoutDashboard },
  { tab: "activity", label: "Activité récente", Icon: Activity },
  { tab: "containers", label: "Conteneurs", Icon: Box },
  { tab: "storage", label: "Stockage", Icon: HardDrive },
  { tab: "reseau", label: "Réseau", Icon: Network },
  { tab: "templates", label: "Modèles / ISO", Icon: Layers },
  { tab: "backups", label: "Sauvegardes", Icon: CalendarClock },
  { tab: "exports", label: "Exports", Icon: PackageOpen },
  { tab: "permissions", label: "Permissions", Icon: ShieldCheck },
  { tab: "automation", label: "Automatisation", Icon: Workflow },
  { tab: "nodes", label: "Nœuds", Icon: Server },
  { tab: "journal", label: "Journal", Icon: ScrollText },
];

export default function SidebarRail() {
  const navigateTo = useInfraStore((s) => s.navigateTo);

  return (
    <div className="flex h-full w-[176px] shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-chrome-950 bg-chrome-900 p-2">
      {ITEMS.map(({ tab, label, Icon }) => (
        <button
          key={tab}
          onClick={() => navigateTo("datacenter", null, tab)}
          className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[12.5px] text-chrome-400 hover:bg-chrome-700 hover:text-chrome-100"
        >
          <Icon size={15} className="shrink-0 text-chrome-400" />
          <span className="truncate">{label}</span>
        </button>
      ))}
    </div>
  );
}
