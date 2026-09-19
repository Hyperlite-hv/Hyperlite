import { useEffect, useState } from "react";
import { LayoutGrid, Table2 } from "lucide-react";
import GaugeRing from "../../components/GaugeRing";
import VMCard from "../../components/VMCard";
import VMTable from "../../components/VMTable";
import { fetchHostMetricsHistory } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { formatUptime, formatMo, formatGo } from "../../utils/format";

// Real CPU/RAM through GET /host/metrics/history (continuous collection), the
// same source as NodeSystemTab.jsx, no simulated data.
export default function NodeSummaryTab({ resource: node }) {
  const vms = useInfraStore((s) => s.vms);
  const [latest, setLatest] = useState(null);
  const [view, setView] = useState("cards"); // "cards" | "table"

  useEffect(() => {
    const load = () => fetchHostMetricsHistory("1h").then((rows) => setLatest(rows[rows.length - 1] || null)).catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  if (!node) return null;

  const nodeVms = vms.filter((v) => v.node === node.id);
  const cpuRatio = latest?.cpu_pct != null ? latest.cpu_pct / 100 : null;
  const ramRatio = latest?.mem_total_mb ? (latest.mem_used_mb ?? 0) / latest.mem_total_mb : null;
  const diskRatio = node.stockage_total_go != null && node.stockage_utilise_go != null
    ? node.stockage_utilise_go / node.stockage_total_go : null;

  return (
    <div className="space-y-5">
      <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
        <GaugeRing
          label="Processeur" ratio={cpuRatio}
          valueLabel={cpuRatio != null ? `${Math.round(cpuRatio * 100)} %` : "n/a"}
          colorClass="text-accent-blue"
        />
        <GaugeRing
          label="Memory" ratio={ramRatio}
          valueLabel={latest?.mem_total_mb ? `${Math.round(latest.mem_used_mb)} / ${Math.round(latest.mem_total_mb)} MB` : "n/a"}
          colorClass="text-accent-orange"
        />
        {diskRatio != null ? (
          <GaugeRing label="Storage" ratio={diskRatio} valueLabel={`${formatGo(node.stockage_utilise_go)} / ${formatGo(node.stockage_total_go)}`} colorClass="text-accent-green" />
        ) : (
          <GaugeRing label="Storage" ratio={null} />
        )}
      </div>

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Status</h3>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-anthracite-400 text-xs">Uptime</dt><dd className="text-anthracite-100">{formatUptime(node.uptime_s)}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Available RAM</dt><dd className="text-anthracite-100">{node.memoire_disponible_mo != null ? formatMo(node.memoire_disponible_mo) : "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">IP address</dt><dd className="text-anthracite-100">{node.ip || "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Version</dt><dd className="text-anthracite-100">{node.version || "--"}</dd></div>
        </dl>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <span className="text-[15px] font-semibold text-anthracite-100">Virtual machines</span>
          <span className="font-mono text-xs text-anthracite-400">{nodeVms.length}</span>
          <div className="ml-auto flex gap-0.5 rounded-md border border-anthracite-600 bg-anthracite-900 p-0.5">
            <button
              onClick={() => setView("cards")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium ${view === "cards" ? "bg-anthracite-700 text-anthracite-100" : "text-anthracite-400 hover:text-anthracite-100"}`}
            >
              <LayoutGrid size={13} /> Cards
            </button>
            <button
              onClick={() => setView("table")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium ${view === "table" ? "bg-anthracite-700 text-anthracite-100" : "text-anthracite-400 hover:text-anthracite-100"}`}
            >
              <Table2 size={13} /> Table view
            </button>
          </div>
        </div>
        {nodeVms.length === 0 ? (
          <div className="card p-4 text-sm text-anthracite-400">No VMs on this node.</div>
        ) : view === "table" ? (
          <VMTable vms={nodeVms} />
        ) : (
          <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
            {nodeVms.map((vm) => <VMCard key={vm.nom} vm={vm} />)}
          </div>
        )}
      </div>
    </div>
  );
}
