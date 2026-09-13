import { useEffect, useState } from "react";
import MetricChart from "./MetricChart";
import { chartColors } from "../theme/colors";
import { formatMo } from "../utils/format";

const RANGES = [
  { key: "1h", label: "1 h" },
  { key: "24h", label: "24 h" },
  { key: "7j", label: "Semaine" },
  { key: "30j", label: "Mois" },
];

// Reel : GET /vms/{name}/metrics/history ou /host/metrics/history (voir
// app/routers/metrics.py, chantier 10) -- historique PERSISTE cote backend
// (1h = echantillons bruts/15s, 24h/semaine/mois = moyennes horaires), par
// opposition au graphe "session en cours" de useLiveVMMetrics qui ne garde
// que les ~8 dernieres minutes en memoire navigateur.
export default function MetricsHistoryCard({ title, fetcher }) {
  const [range, setRange] = useState("1h");
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetcher(range)
      .then((r) => { if (!cancelled) setRows(r); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [range, fetcher]);

  const data = (rows || []).map((r) => ({
    t: new Date(r.ts).getTime(),
    cpu: (r.cpu_pct ?? 0) / 100,
    ramMo: r.mem_used_mb,
    ramTotalMo: r.mem_total_mb,
    netIn: (r.net_rx_bps ?? 0) / 1024,
    netOut: (r.net_tx_bps ?? 0) / 1024,
  }));

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-anthracite-100">{title}</h3>
        <div className="flex gap-0.5 rounded-md bg-anthracite-700 p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              className={`rounded px-2.5 py-1 text-xs font-medium ${range === r.key ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="mt-2 text-xs text-status-error">{error}</p>}
      {rows != null && rows.length === 0 && <p className="mt-3 text-sm text-anthracite-400">Pas encore assez d'historique pour cette période.</p>}
      {rows != null && rows.length > 0 && (
        <div className="mt-3">
          <MetricChart data={data} series={[{ key: "cpu", label: "CPU", color: chartColors.cpu }]} yFormatter={(v) => `${Math.round(v * 100)}%`} height={140} />
        </div>
      )}
    </div>
  );
}
