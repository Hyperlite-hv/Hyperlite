import LoadingState from "./LoadingState";
import { useEffect, useState } from "react";
import { useInfraStore } from "../store/useInfraStore";
import { useLiveVMMetrics } from "../hooks/useLiveVMMetrics";
import { fetchSnapshots } from "../api/client";
import { statusColor } from "../theme/colors";
import { formatUptime, formatMo, formatGo } from "../utils/format";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";

// Side panel opened on double-click of a VM card: a quick preview without leaving
// the current page, with a link to the existing full page (VM_TABS,
// CentralPanel.jsx) to go further. This panel does not duplicate the
// Console/Hardware/etc. tabs, it only points to them.
export default function VMDetailPanel({ vm, onClose }) {
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const active = vm.etat === "actif";
  const { current } = useLiveVMMetrics(vm.nom, active);
  const [snapshots, setSnapshots] = useState(null);

  useEffect(() => {
    fetchSnapshots(vm.nom).then(setSnapshots).catch(() => setSnapshots([]));
  }, [vm.nom]);

  function openFullPage(tab = "summary") {
    navigateTo("vm", vm.nom, tab);
    onClose();
  }

  return (
    <Sheet open onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent className="w-[400px] sm:max-w-[400px] p-0 gap-0">
        <SheetHeader className="flex flex-col gap-2.5 border-b border-border px-5 py-4 space-y-0">
          <SheetTitle className="text-[17px] font-bold tracking-tight text-foreground">{vm.nom}</SheetTitle>
          <span className="flex items-center gap-1.5 text-xs" style={{ color: active ? "#48D6C6" : "#B6B0DE" }}>
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: statusColor(vm.etat) }} />
            {active ? "Running" : "Stopped"}{vm.uptime_s ? ` · ${formatUptime(vm.uptime_s)}` : ""}{vm.ip ? ` · ${vm.ip}` : ""}
          </span>
          <div className="mt-0.5 flex gap-1.5">
            <Button className="flex-1 justify-center" onClick={() => openFullPage("console")}>Console</Button>
            <Button variant="secondary" className="flex-1 justify-center" onClick={() => openFullPage("snapshots")}>Snapshot</Button>
            {active ? (
              <Button variant="outline" className="text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => runVMAction(vm.nom, "stop").catch(() => {})}>Stop</Button>
            ) : (
              <Button onClick={() => runVMAction(vm.nom, "start").catch(() => {})}>Start</Button>
            )}
          </div>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">
          <div className="flex flex-col gap-2.5">
            <span className="font-mono text-[10px] tracking-wider text-muted-foreground">HARDWARE</span>
            <Row label="Processeur" value={`${vm.vcpu} vCPU`} />
            <Row label="Memory" value={formatMo(vm.memoire_mo)} />
            <Row label="Disk" value={vm.disque_go != null ? formatGo(vm.disque_go) : "--"} />
          </div>

          <div className="flex flex-col gap-2.5">
            <span className="font-mono text-[10px] tracking-wider text-muted-foreground">LOAD</span>
            {active && current ? (
              <>
                <Bar label="CPU" ratio={current.cpu} valueLabel={`${Math.round(current.cpu * 100)} %`} color="#8B7CF6" />
                <Bar
                  label="Memory"
                  ratio={current.ramAlloueeMo ? (current.ramUseeMo ?? 0) / current.ramAlloueeMo : 0}
                  valueLabel={current.ramAlloueeMo ? `${Math.round(current.ramUseeMo)} / ${Math.round(current.ramAlloueeMo)} MB` : "--"}
                  color="#F5A04B"
                />
              </>
            ) : (
              <span className="text-xs text-muted-foreground">{active ? "Loading..." : "VM stopped"}</span>
            )}
          </div>

          <div className="flex flex-col gap-2.5">
            <span className="font-mono text-[10px] tracking-wider text-muted-foreground">
              SNAPSHOTS{snapshots ? ` · ${snapshots.length}` : ""}
            </span>
            {snapshots == null ? (
              <span className="text-xs text-muted-foreground"><LoadingState /></span>
            ) : snapshots.length === 0 ? (
              <span className="text-xs text-muted-foreground">No snapshots.</span>
            ) : (
              snapshots.slice(0, 4).map((s) => (
                <div key={s.nom} className="flex justify-between text-[12.5px]">
                  <span className="text-foreground">{s.nom}</span>
                  <span className="font-mono text-[11px] text-muted-foreground">{s.date_creation || ""}</span>
                </div>
              ))
            )}
          </div>

          <button className="mt-auto text-left text-xs text-accent-blue hover:underline" onClick={() => openFullPage("summary")}>
            Open the full page →
          </button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between text-[12.5px]">
      <span className="text-foreground/80">{label}</span>
      <span className="font-mono text-[12px] text-foreground">{value}</span>
    </div>
  );
}

function Bar({ label, ratio, valueLabel, color }) {
  const pct = Math.max(0, Math.min(1, ratio || 0)) * 100;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between text-xs">
        <span className="text-foreground/80">{label}</span>
        <span className="font-mono text-foreground">{valueLabel}</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full transition-[width] duration-300" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
    </div>
  );
}
