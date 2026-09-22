import { useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";
import { statusColor } from "../theme/colors";
import { formatUptime, formatMo } from "../utils/format";
import VMActionMenu from "./VMActionMenu";
import VMDetailPanel from "./VMDetailPanel";
import { Button } from "@/components/ui/button";

const STATE_LABELS = { actif: "Running", arrete: "Stopped", suspendu: "Suspended" };

// Actionable VM card: the "···" button opens the actions menu (VMActionMenu, a
// real shadcn DropdownMenu); a double-click opens the side panel
// (VMDetailPanel). The keyboard (⌘K) stays available as well, see SearchBar.jsx
// for the existing palette.
export default function VMCard({ vm }) {
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const [panelOpen, setPanelOpen] = useState(false);
  const active = vm.etat === "actif";

  return (
    <>
      <div
        onDoubleClick={() => setPanelOpen(true)}
        className="flex cursor-pointer flex-col gap-3.5 rounded-lg border border-border bg-card p-4 transition-colors hover:border-muted-foreground/40"
      >
        <div className="flex items-start gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="truncate text-[14.5px] font-semibold text-foreground">{vm.nom}</span>
            <span className="flex items-center gap-1.5 text-xs" style={{ color: active ? "#48D6C6" : vm.etat === "suspendu" ? "#F5A04B" : "#9A94C4" }}>
              <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: statusColor(vm.etat) }} />
              {STATE_LABELS[vm.etat] || vm.etat}{active && vm.uptime_s ? ` · ${formatUptime(vm.uptime_s)}` : ""}
            </span>
          </div>
          <VMActionMenu vm={vm}>
            <Button
              aria-label="More actions"
              variant="ghost"
              size="icon"
              className="ml-auto size-7 shrink-0 text-muted-foreground hover:text-foreground"
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal size={16} />
            </Button>
          </VMActionMenu>
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

      {panelOpen && <VMDetailPanel vm={vm} onClose={() => setPanelOpen(false)} />}
    </>
  );
}

function Stat({ label, value, mono }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="font-mono text-[10px] tracking-wider text-muted-foreground">{label}</span>
      <span className={`truncate text-[13px] text-foreground ${mono ? "font-mono text-xs!" : ""}`}>{value}</span>
    </div>
  );
}

function CardButton({ children, onClick, primary, danger }) {
  return (
    <Button
      onClick={onClick}
      variant={danger ? "outline" : primary ? "default" : "secondary"}
      className={`flex-1 justify-center px-0! py-1.5! text-[12.5px] ${danger ? "text-status-error border-status-error/30 hover:bg-status-error/10" : ""}`}
    >
      {children}
    </Button>
  );
}
