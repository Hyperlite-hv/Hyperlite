import GaugeRing from "../../components/GaugeRing";
import StatusBadge from "../../components/StatusBadge";
import { useInfraStore } from "../../store/useInfraStore";
import { formatMo, formatGo } from "../../utils/format";

export default function DatacenterSummaryTab() {
  const { nodes, vms, containers } = useInfraStore((s) => ({ nodes: s.nodes, vms: s.vms, containers: s.containers }));

  const totalRamMo = nodes.reduce((a, n) => a + n.memoire_totale_mo, 0);
  const usedRamMo = nodes.reduce((a, n) => a + n.memoire_utilisee_mo, 0);
  const totalDiskGo = nodes.reduce((a, n) => a + n.stockage_total_go, 0);
  const usedDiskGo = nodes.reduce((a, n) => a + n.stockage_utilise_go, 0);
  const avgCpu = nodes.length ? nodes.reduce((a, n) => a + n.cpu_utilisation, 0) / nodes.length : 0;

  const allResources = [...vms, ...containers];
  const counts = {
    actif: allResources.filter((r) => r.etat === "actif").length,
    arrete: allResources.filter((r) => r.etat === "arrete").length,
    avertissement: allResources.filter((r) => r.etat === "avertissement").length,
    erreur: allResources.filter((r) => r.etat === "erreur").length,
  };

  return (
    <div className="space-y-5">
      <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
        <GaugeRing label="CPU moyen" ratio={avgCpu} valueLabel={`${nodes.length} noeud(s)`} colorClass="text-accent-blue" />
        <GaugeRing label="RAM" ratio={usedRamMo / totalRamMo} valueLabel={`${formatMo(usedRamMo)} / ${formatMo(totalRamMo)}`} colorClass="text-accent-orange" />
        <GaugeRing label="Stockage" ratio={usedDiskGo / totalDiskGo} valueLabel={`${formatGo(usedDiskGo)} / ${formatGo(totalDiskGo)}`} colorClass="text-accent-green" />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Object.entries(counts).map(([etat, count]) => (
          <div key={etat} className="card p-4">
            <div className="flex items-center gap-2"><StatusBadge etat={etat} /></div>
            <div className="mt-2 text-2xl font-semibold text-anthracite-100">{count}</div>
          </div>
        ))}
      </div>

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Noeuds</h3>
        <div className="divide-y divide-anthracite-600">
          {nodes.map((n) => (
            <div key={n.id} className="flex items-center gap-3 py-2 text-sm">
              <StatusBadge etat={n.etat} showLabel={false} />
              <span className="text-anthracite-100 flex-1">{n.nom}</span>
              {!n.reel && <span className="text-[10px] text-accent-orange border border-accent-orange/40 rounded px-1.5 py-0.5">fictif</span>}
              <span className="text-anthracite-400 text-xs">{Math.round(n.cpu_utilisation * 100)}% CPU</span>
              <span className="text-anthracite-400 text-xs">{formatMo(n.memoire_utilisee_mo)} / {formatMo(n.memoire_totale_mo)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
