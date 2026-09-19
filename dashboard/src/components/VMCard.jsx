import { useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";
import { statusColor } from "../theme/colors";
import { formatUptime, formatMo } from "../utils/format";
import VMActionMenu from "./VMActionMenu";
import VMDetailPanel from "./VMDetailPanel";

const STATE_LABELS = { actif: "Running", arrete: "Stopped", suspendu: "Suspended" };

// Actionable VM card: a click anywhere on the card (or its "···") opens the
// anchored actions menu (VMActionMenu); a double-click opens the side panel
// (VMDetailPanel). The keyboard (⌘K) stays available as well, it is no longer the
// only path, see SearchBar.jsx for the existing palette.
export default function VMCard({ vm }) {
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const cardRef = useRef(null);
  const active = vm.etat === "actif";

  function openMenu(e) {
    e.stopPropagation();
    setMenuAnchor(cardRef.current.getBoundingClientRect());
  }

  return (
    <>
      <div
        ref={cardRef}
        onClick={openMenu}
        onDoubleClick={() => setPanelOpen(true)}
        className={`flex cursor-pointer flex-col gap-3.5 rounded-lg border p-4 transition-colors ${
          menuAnchor ? "border-accent-blue bg-anthracite-700" : "border-anthracite-600 bg-anthracite-800 hover:border-anthracite-500"
        }`}
      >
        <div className="flex items-start gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="truncate text-[14.5px] font-semibold text-anthracite-100">{vm.nom}</span>
            <span className="flex items-center gap-1.5 text-xs" style={{ color: active ? "#48D6C6" : vm.etat === "suspendu" ? "#F5A04B" : "#9A94C4" }}>
              <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: statusColor(vm.etat) }} />
              {STATE_LABELS[vm.etat] || vm.etat}{active && vm.uptime_s ? ` · ${formatUptime(vm.uptime_s)}` : ""}
            </span>
          </div>
          <button onClick={openMenu} className="ml-auto shrink-0 text-anthracite-400 hover:text-anthracite-100">
            <MoreHorizontal size={16} />
          </button>
        </div>

        <div className="flex gap-4">
          <Stat label="vCPU" value={vm.vcpu} />
          <Stat label="RAM" value={formatMo(vm.memoire_mo)} />
          <Stat label="IP" value={vm.ip || "—"} mono />
        </div>

        <div className="flex gap-1.5" onClick={(e) => e.stopPropagation()}>
          {active ? (
            <>
              <CardButton onClick={() => useInfraStore.getState().navigateTo("vm", vm.nom, "console")}>Console</CardButton>
              <CardButton onClick={() => useInfraStore.getState().navigateTo("vm", vm.nom, "snapshots")}>Snapshot</CardButton>
              <CardButton danger onClick={() => runVMAction(vm.nom, "stop").catch(() => {})}>Stop</CardButton>
            </>
          ) : (
            <>
              <CardButton primary onClick={() => runVMAction(vm.nom, "start").catch(() => {})}>Start</CardButton>
              <CardButton onClick={() => useInfraStore.getState().navigateTo("vm", vm.nom, "options")}>Clone</CardButton>
              <CardButton onClick={() => useInfraStore.getState().navigateTo("vm", vm.nom, "hardware")}>Edit</CardButton>
            </>
          )}
        </div>
      </div>

      {menuAnchor && <VMActionMenu vm={vm} anchorRect={menuAnchor} onClose={() => setMenuAnchor(null)} />}
      {panelOpen && <VMDetailPanel vm={vm} onClose={() => setPanelOpen(false)} />}
    </>
  );
}

function Stat({ label, value, mono }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="font-mono text-[10px] tracking-wider text-anthracite-400">{label}</span>
      <span className={`truncate text-[13px] text-anthracite-100 ${mono ? "font-mono !text-xs" : ""}`}>{value}</span>
    </div>
  );
}

function CardButton({ children, onClick, primary, danger }) {
  const cls = danger ? "btn-danger" : primary ? "btn-primary" : "btn-secondary";
  return (
    <button onClick={onClick} className={`${cls} flex-1 justify-center !px-0 !py-1.5 text-[12.5px]`}>
      {children}
    </button>
  );
}
