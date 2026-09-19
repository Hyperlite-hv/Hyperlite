import { X } from "lucide-react";
import HyperliteLogo from "../components/HyperliteLogo";
import SidebarRail from "./SidebarRail";
import ResourceTree from "./ResourceTree";
import { useInfraStore } from "../store/useInfraStore";

// Unified side column: logo + navigation rail + Datacenter tree in a single
// indigo band. From `md` up, always visible as a fixed column (original
// behaviour). Below that (phone), it becomes an overlaid drawer (fixed +
// backdrop) controlled by mobileSidebarOpen. Real bug found when testing at phone
// width: the fixed 268px column took the whole screen, leaving no usable content.
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
            <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-chrome-400">Hypervisor</div>
          </div>
          <button className="ml-auto rounded-md p-1.5 text-chrome-400 hover:bg-white/6 hover:text-chrome-100 md:hidden" onClick={close} aria-label="Close navigation">
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
