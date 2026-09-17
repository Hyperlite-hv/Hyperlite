import {
  LayoutDashboard, Activity, Box, HardDrive, Network, Layers,
  CalendarClock, PackageOpen, ShieldCheck, Workflow, Server, ScrollText, LifeBuoy,
} from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

// Rail de navigation façon Proxmox VE (calque sur DATACENTER_TABS,
// CentralPanel.jsx -- une entree par section reellement disponible cote
// Hyperlite, pas la liste complete de Proxmox qui n'existe pas ici).
// Purement additif : ne remplace pas l'arbre Datacenter > Nœud > VM
// (ResourceTree, juste en dessous dans la meme colonne depuis la refonte
// 2026-09-17) qui reste la navigation/les raccourcis VM existants -- ce
// rail ne fait que sauter directement a un onglet Datacenter donne sans
// passer par l'arbre.
const TOP_ITEMS = [
  { tab: "summary", label: "Tableau de bord", Icon: LayoutDashboard },
  { tab: "activity", label: "Activité récente", Icon: Activity },
];
const GROUPS = [
  {
    label: "Ressources",
    items: [
      { tab: "containers", label: "Conteneurs", Icon: Box },
      { tab: "storage", label: "Stockage", Icon: HardDrive },
      { tab: "reseau", label: "Réseau", Icon: Network },
      { tab: "templates", label: "Modèles / ISO", Icon: Layers },
      { tab: "nodes", label: "Nœuds", Icon: Server },
      { tab: "ha", label: "HA", Icon: LifeBuoy },
    ],
  },
  {
    label: "Exploitation",
    items: [
      { tab: "backups", label: "Sauvegardes", Icon: CalendarClock },
      { tab: "exports", label: "Exports", Icon: PackageOpen },
      { tab: "automation", label: "Automatisation", Icon: Workflow },
      { tab: "permissions", label: "Permissions", Icon: ShieldCheck },
      { tab: "journal", label: "Journal", Icon: ScrollText },
    ],
  },
];

function NavItem({ tab, label, Icon }) {
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const closeMobileSidebar = useInfraStore((s) => s.closeMobileSidebar);
  const isActive = useInfraStore((s) => s.selection.type === "datacenter" && s.activeTab === tab);

  return (
    <button
      onClick={() => { navigateTo("datacenter", null, tab); closeMobileSidebar(); }}
      className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12.5px] font-semibold transition-colors ${
        isActive
          ? "bg-accent-blue text-white shadow-[0_4px_14px_rgba(79,70,229,0.35)]"
          : "text-chrome-400 hover:bg-white/[0.06] hover:text-chrome-100"
      }`}
    >
      <Icon size={16} className="shrink-0" />
      <span className="truncate">{label}</span>
    </button>
  );
}

export default function SidebarRail() {
  return (
    <div className="flex shrink-0 flex-col gap-0.5 p-2.5 pb-1">
      {TOP_ITEMS.map((item) => <NavItem key={item.tab} {...item} />)}
      {GROUPS.map((group) => (
        <div key={group.label} className="mt-1">
          <div className="px-3 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-chrome-400/70">
            {group.label}
          </div>
          <div className="flex flex-col gap-0.5">
            {group.items.map((item) => <NavItem key={item.tab} {...item} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
