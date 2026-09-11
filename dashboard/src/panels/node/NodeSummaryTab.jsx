import { useState } from "react";
import GaugeRing from "../../components/GaugeRing";
import MetricChart from "../../components/MetricChart";
import RangeToggle from "../../components/RangeToggle";
import { useSimulatedMetrics } from "../../hooks/useSimulatedMetrics";
import { chartColors } from "../../theme/colors";
import { formatUptime, formatMo, formatGo } from "../../utils/format";

export default function NodeSummaryTab({ resource: node }) {
  const [range, setRange] = useState(1);
  const { data, current } = useSimulatedMetrics(node?.id, range, {
    cpu: node?.cpu_utilisation, ram: node ? node.memoire_utilisee_mo / node.memoire_totale_mo : 0.4,
  });
  if (!node) return null;

  const ramRatio = node.memoire_utilisee_mo / node.memoire_totale_mo;
  const diskRatio = node.stockage_utilise_go / node.stockage_total_go;

  return (
    <div className="space-y-5">
      {!node.reel && (
        <div className="rounded-md border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
          Noeud fictif -- illustre le multi-node vise par la structure Proxmox. Seul <code>kvm-lab</code> correspond a un vrai serveur Hyperlite aujourd'hui.
        </div>
      )}

      <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
        <GaugeRing label="CPU" ratio={current.cpu} valueLabel={`${node.cpu_coeurs} coeurs`} colorClass="text-accent-blue" />
        <GaugeRing label="RAM" ratio={ramRatio} valueLabel={`${formatMo(node.memoire_utilisee_mo)} / ${formatMo(node.memoire_totale_mo)}`} colorClass="text-accent-orange" />
        <GaugeRing label="Stockage" ratio={diskRatio} valueLabel={`${formatGo(node.stockage_utilise_go)} / ${formatGo(node.stockage_total_go)}`} colorClass="text-accent-green" />
      </div>

      <div className="card p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-anthracite-100">CPU & RAM</h3>
          <RangeToggle value={range} onChange={setRange} />
        </div>
        <div className="mt-3">
          <MetricChart
            data={data}
            series={[
              { key: "cpu", label: "CPU", color: chartColors.cpu },
              { key: "ram", label: "RAM", color: chartColors.ram },
            ]}
            yFormatter={(v) => `${Math.round(v * 100)}%`}
          />
        </div>
      </div>

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Statut</h3>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-anthracite-400 text-xs">Uptime</dt><dd className="text-anthracite-100">{formatUptime(node.uptime_s)}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Adresse IP</dt><dd className="text-anthracite-100">{node.ip}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">OS</dt><dd className="text-anthracite-100">{node.os}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Version</dt><dd className="text-anthracite-100">{node.version}</dd></div>
        </dl>
      </div>
    </div>
  );
}
