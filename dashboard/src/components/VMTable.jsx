import { useMemo, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";
import { statusColor } from "../theme/colors";
import { formatMo, formatGo } from "../utils/format";
import VMActionMenu from "./VMActionMenu";
import VMDetailPanel from "./VMDetailPanel";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";

const STATE_LABELS = { actif: "Running", arrete: "Stopped", suspendu: "Suspended" };
const COLUMNS = "34px minmax(0,1.5fr) 96px 64px 96px minmax(0,1fr) 84px 40px";

// Dense table view: the same data as the card grid (VMCard.jsx), in a compact
// layout for browsing many VMs at once. It toggles side by side with the card view
// at the top of the "Virtual machines" section (NodeSummaryTab.jsx).
export default function VMTable({ vms }) {
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [panelVm, setPanelVm] = useState(null);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return vms;
    return vms.filter((v) => v.nom.toLowerCase().includes(q) || (v.ip || "").includes(q));
  }, [vms, filter]);

  function toggleSelected(nom) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(nom)) next.delete(nom); else next.add(nom);
      return next;
    });
  }

  async function bulkAction(action) {
    const names = [...selected];
    setSelected(new Set());
    for (const nom of names) {
      try { await runVMAction(nom, action); } catch { /* already notified through a toast */ }
    }
  }

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-3.5">
        <div className="flex items-center gap-3">
          <h3 className="text-[16px] font-bold tracking-tight text-foreground">Virtual machines</h3>
          <span className="font-mono text-xs text-muted-foreground">{filtered.length} of {vms.length}</span>
          <Input aria-label="Filter…"
            className="ml-auto w-[200px]" placeholder="Filter…"
            value={filter} onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        {selected.size > 0 && (
          <div className="flex items-center gap-2.5 rounded-md border border-border bg-muted px-3.5 py-2 animate-in fade-in-0 slide-in-from-top-1 duration-150">
            <span className="text-[12.5px] font-semibold text-foreground">{selected.size} selected{selected.size > 1 ? "s" : ""}</span>
            <div className="flex gap-1.5">
              <Button size="sm" variant="secondary" onClick={() => bulkAction("start")}>Start</Button>
              <Button size="sm" variant="secondary" onClick={() => bulkAction("stop")}>Stop</Button>
            </div>
            <span className="ml-auto font-mono text-[11px] text-muted-foreground">Esc to cancel</span>
          </div>
        )}
      </div>

      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Virtual machines table">
        <div style={{ display: "grid", gridTemplateColumns: COLUMNS }} className="border-b border-border px-3.5 py-2 font-mono text-[10px] tracking-wider text-muted-foreground">
          <span />
          <span>NAME</span><span>STATE</span><span>vCPU</span><span>MEMORY</span><span>IP ADDRESS</span><span>DISK</span><span />
        </div>
        {filtered.length === 0 && <div className="px-4 py-6 text-center text-sm text-muted-foreground">No VMs.</div>}
        {filtered.map((vm) => (
          <div
            key={vm.nom}
            style={{ display: "grid", gridTemplateColumns: COLUMNS }}
            className="items-center border-b border-border px-3.5 py-2.5 text-[13px] last:border-0 transition-colors duration-150 hover:bg-muted/50 cursor-pointer"
            onDoubleClick={() => setPanelVm(vm)}
          >
            <Checkbox aria-label={`Select ${vm.nom}`}
              checked={selected.has(vm.nom)} onCheckedChange={() => toggleSelected(vm.nom)}
              onClick={(e) => e.stopPropagation()}
            />
            <span className="flex items-center gap-2 truncate font-medium text-foreground">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: statusColor(vm.etat) }} />
              {vm.nom}
            </span>
            <span style={{ color: vm.etat === "actif" ? "#48D6C6" : vm.etat === "suspendu" ? "#F5A04B" : "#9A94C4" }}>
              {STATE_LABELS[vm.etat] || vm.etat}
            </span>
            <span className="font-mono text-foreground/80">{vm.vcpu}</span>
            <span className="font-mono text-foreground/80">{formatMo(vm.memoire_mo)}</span>
            <span className="truncate font-mono text-foreground/80">{vm.ip || "—"}</span>
            <span className="font-mono text-foreground/80">{vm.disque_go != null ? formatGo(vm.disque_go) : "—"}</span>
            <VMActionMenu vm={vm}>
              <Button
                aria-label="More actions"
                variant="ghost"
                size="icon"
                className="size-7 justify-self-end text-muted-foreground hover:text-foreground"
                onClick={(e) => e.stopPropagation()}
              >
                <MoreHorizontal size={15} />
              </Button>
            </VMActionMenu>
          </div>
        ))}
      </div>
      <div className="px-4 py-2.5 font-mono text-[10.5px] text-muted-foreground">
        Cards / table: the same data. Toggle above this list.
      </div>

      {panelVm && <VMDetailPanel vm={panelVm} onClose={() => setPanelVm(null)} />}
    </Card>
  );
}
