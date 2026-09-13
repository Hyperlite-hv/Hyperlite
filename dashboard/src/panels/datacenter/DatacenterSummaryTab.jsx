import GaugeRing from "../../components/GaugeRing";
import StatusBadge from "../../components/StatusBadge";
import { useInfraStore } from "../../store/useInfraStore";
import { formatMo, formatGo } from "../../utils/format";

function sumDefined(items, key) {
  const defined = items.filter((i) => i[key] != null);
  if (!defined.length) return null;
  return defined.reduce((a, i) => a + i[key], 0);
}

export default function DatacenterSummaryTab() {
  const { nodes, vms } = useInfraStore((s) => ({ nodes: s.nodes, vms: s.vms }));

  const totalRamMo = sumDefined(nodes, "memoire_totale_mo");
  const usedRamMo = sumDefined(nodes, "memoire_utilisee_mo");
  const totalDiskGo = sumDefined(nodes, "stockage_total_go");
  const usedDiskGo = sumDefined(nodes, "stockage_utilise_go");
  const cpuNodes = nodes.filter((n) => n.cpu_utilisation != null);
  const avgCpu = cpuNodes.length ? cpuNodes.reduce((a, n) => a + n.cpu_utilisation, 0) / cpuNodes.length : null;

  const counts = {
    actif: vms.filter((r) => r.etat === "actif").length,
    arrete: vms.filter((r) => r.etat === "arrete").length,
    avertissement: vms.filter((r) => r.etat === "avertissement").length,
    erreur: vms.filter((r) => r.etat === "erreur").length,
  };

  return (
    <div className="space-y-5">
      <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
        <GaugeRing label="CPU moyen" ratio={avgCpu} valueLabel={`${nodes.length} nœud(s)`} colorClass="text-accent-blue" />
        <GaugeRing
          label="RAM" ratio={totalRamMo != null && usedRamMo != null ? usedRamMo / totalRamMo : null}
          valueLabel={totalRamMo != null && usedRamMo != null ? `${formatMo(usedRamMo)} / ${formatMo(totalRamMo)}` : "Non exposé par /dashboard"}
          colorClass="text-accent-orange"
        />
        <GaugeRing
          label="Stockage" ratio={totalDiskGo != null && usedDiskGo != null ? usedDiskGo / totalDiskGo : null}
          valueLabel={totalDiskGo != null && usedDiskGo != null ? `${formatGo(usedDiskGo)} / ${formatGo(totalDiskGo)}` : undefined}
          colorClass="text-accent-green"
        />
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
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Nœuds</h3>
        <div className="divide-y divide-anthracite-600">
          {nodes.map((n) => (
            <div key={n.id} className="flex items-center gap-3 py-2 text-sm">
              <StatusBadge etat={n.etat} showLabel={false} />
              <span className="text-anthracite-100 flex-1">{n.nom}</span>
              <span className="text-anthracite-400 text-xs">{n.cpu_utilisation != null ? `${Math.round(n.cpu_utilisation * 100)}% CPU` : "CPU n/a"}</span>
              <span className="text-anthracite-400 text-xs">{n.memoire_totale_mo != null ? `${formatMo(n.memoire_utilisee_mo)} / ${formatMo(n.memoire_totale_mo)}` : "RAM n/a"}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
