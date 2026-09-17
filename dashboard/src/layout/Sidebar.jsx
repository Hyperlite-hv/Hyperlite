import { X } from "lucide-react";
import HyperliteLogo from "../components/HyperliteLogo";
import SidebarRail from "./SidebarRail";
import ResourceTree from "./ResourceTree";
import { useInfraStore } from "../store/useInfraStore";

// Colonne laterale unifiee (refonte 2026-09-17) : logo + rail de navigation
// + arbre Datacenter dans un seul bandeau indigo. A partir de `md`, toujours
// visible en colonne fixe (comportement d'origine). En dessous (telephone),
// devient un tiroir superpose (fixed + backdrop) controle par
// mobileSidebarOpen -- BUG REEL trouve en testant a largeur telephone : la
// colonne 268px fixe prenait tout l'ecran, plus aucun contenu utilisable.
export default function Sidebar() {
  const mobileOpen = useInfraStore((s) => s.mobileSidebarOpen);
  const close = useInfraStore((s) => s.closeMobileSidebar);

  return (
    <>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 md:hidden" onClick={close} />
      )}
      <div
        className={`fixed inset-y-0 left-0 z-50 flex h-full w-[268px] shrink-0 flex-col bg-chrome-900 border-r border-chrome-950 transition-transform duration-200 md:static md:z-auto md:translate-x-0 ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex shrink-0 items-center gap-2.5 px-4 py-3.5">
          <HyperliteLogo size={28} />
          <div className="leading-tight">
            <div className="text-[15px] font-extrabold tracking-tight text-white">Hyperlite</div>
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-chrome-400">Hyperviseur</div>
          </div>
          <button className="ml-auto rounded-md p-1.5 text-chrome-400 hover:bg-white/[0.06] hover:text-chrome-100 md:hidden" onClick={close} aria-label="Fermer la navigation">
            <X size={17} />
          </button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <SidebarRail />
          <ResourceTree />
        </div>
      </div>
    </>
  );
}
