import HyperliteLogo from "../components/HyperliteLogo";
import SidebarRail from "./SidebarRail";
import ResourceTree from "./ResourceTree";

// Colonne laterale unifiee (refonte 2026-09-17) : logo + rail de navigation
// + arbre Datacenter dans un seul bandeau indigo pleine hauteur, calque sur
// la direction visuelle validee par Antho (maquette artefact). Remplace
// l'ancienne paire de colonnes separees (SidebarRail 176px + ResourceTree
// 236px, chacune avec sa propre bordure) sans rien retirer : SidebarRail et
// ResourceTree gardent exactement le meme comportement/etat (store), ils
// sont juste rendus l'un sous l'autre dans un seul conteneur scrollable.
export default function Sidebar() {
  return (
    <div className="flex h-full w-[268px] shrink-0 flex-col bg-chrome-900 border-r border-chrome-950">
      <div className="flex shrink-0 items-center gap-2.5 px-4 py-3.5">
        <HyperliteLogo size={28} />
        <div className="leading-tight">
          <div className="text-[15px] font-extrabold tracking-tight text-white">Hyperlite</div>
          <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-chrome-400">Hyperviseur</div>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <SidebarRail />
        <ResourceTree />
      </div>
    </div>
  );
}
