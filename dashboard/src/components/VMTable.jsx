import { useMemo, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";
import { statusColor } from "../theme/colors";
import { formatMo, formatGo } from "../utils/format";
import VMActionMenu from "./VMActionMenu";
import VMDetailPanel from "./VMDetailPanel";

const STATE_LABELS = { actif: "En marche", arrete: "Arrêtée", suspendu: "Suspendue" };
const COLUMNS = "34px minmax(0,1.5fr) 96px 64px 96px minmax(0,1fr) 84px 40px";

// Vue tableau dense (ecran 5b de la refonte 2026-09-13) : memes donnees que
// la grille de cartes (VMCard.jsx), presentation compacte pour parcourir
// beaucoup de VM d'un coup -- bascule cote a cote avec la vue cartes en
// haut de la section "Machines virtuelles" (NodeSummaryTab.jsx), "memes
// donnees" comme le precise la maquette.
export default function VMTable({ vms }) {
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [menu, setMenu] = useState(null); // { vm, anchorRect }
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
      try { await runVMAction(nom, action); } catch (e) { /* deja notifie via toast */ }
    }
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-anthracite-600 px-4 py-3.5">
        <div className="flex items-center gap-3">
          <h3 className="text-[16px] font-bold tracking-tight text-anthracite-100">Machines virtuelles</h3>
          <span className="font-mono text-xs text-anthracite-400">{filtered.length} sur {vms.length}</span>
          <input
            className="input ml-auto w-[200px]" placeholder="Filtrer…"
            value={filter} onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        {selected.size > 0 && (
          <div className="flex items-center gap-2.5 rounded-md border border-anthracite-500 bg-anthracite-700 px-3.5 py-2">
            <span className="text-[12.5px] font-semibold text-anthracite-100">{selected.size} sélectionnée{selected.size > 1 ? "s" : ""}</span>
            <div className="flex gap-1.5">
              <button className="btn-secondary !py-1" onClick={() => bulkAction("start")}>Démarrer</button>
              <button className="btn-secondary !py-1" onClick={() => bulkAction("stop")}>Arrêter</button>
            </div>
            <span className="ml-auto font-mono text-[11px] text-anthracite-400">Échap pour annuler</span>
          </div>
        )}
      </div>

      <div className="overflow-x-auto">
        <div style={{ display: "grid", gridTemplateColumns: COLUMNS }} className="border-b border-anthracite-700 px-3.5 py-2 font-mono text-[10px] tracking-wider text-anthracite-400">
          <span />
          <span>NOM</span><span>ÉTAT</span><span>vCPU</span><span>MÉMOIRE</span><span>ADRESSE IP</span><span>DISQUE</span><span />
        </div>
        {filtered.length === 0 && <div className="px-4 py-6 text-center text-sm text-anthracite-400">Aucune VM.</div>}
        {filtered.map((vm) => (
          <div
            key={vm.nom}
            style={{ display: "grid", gridTemplateColumns: COLUMNS }}
            className="items-center border-b border-anthracite-700 px-3.5 py-2.5 text-[13px] last:border-0 hover:bg-anthracite-700/40 cursor-pointer"
            onDoubleClick={() => setPanelVm(vm)}
          >
            <input
              type="checkbox" checked={selected.has(vm.nom)} onChange={() => toggleSelected(vm.nom)}
              onClick={(e) => e.stopPropagation()} className="h-3.5 w-3.5 accent-accent-blue"
            />
            <span className="flex items-center gap-2 truncate font-medium text-anthracite-100">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: statusColor(vm.etat) }} />
              {vm.nom}
            </span>
            <span style={{ color: vm.etat === "actif" ? "#48D6C6" : vm.etat === "suspendu" ? "#F5A04B" : "#9A94C4" }}>
              {STATE_LABELS[vm.etat] || vm.etat}
            </span>
            <span className="font-mono text-anthracite-200">{vm.vcpu}</span>
            <span className="font-mono text-anthracite-200">{formatMo(vm.memoire_mo)}</span>
            <span className="truncate font-mono text-anthracite-200">{vm.ip || "—"}</span>
            <span className="font-mono text-anthracite-200">{vm.disque_go != null ? formatGo(vm.disque_go) : "—"}</span>
            <button
              className="text-right text-anthracite-400 hover:text-anthracite-100"
              onClick={(e) => { e.stopPropagation(); setMenu({ vm, anchorRect: e.currentTarget.getBoundingClientRect() }); }}
            >
              <MoreHorizontal size={15} />
            </button>
          </div>
        ))}
      </div>
      <div className="px-4 py-2.5 font-mono text-[10.5px] text-anthracite-400">
        Cartes / tableau : mêmes données -- basculez au-dessus de cette liste.
      </div>

      {menu && <VMActionMenu vm={menu.vm} anchorRect={menu.anchorRect} onClose={() => setMenu(null)} />}
      {panelVm && <VMDetailPanel vm={panelVm} onClose={() => setPanelVm(null)} />}
    </div>
  );
}
