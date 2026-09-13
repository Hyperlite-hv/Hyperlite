import { useEffect, useState } from "react";
import GaugeRing from "../../components/GaugeRing";
import MetricsHistoryCard from "../../components/MetricsHistoryCard";
import { fetchHostMetricsHistory } from "../../api/client";

// Reel, depuis GET /health (introspection platform/sys/fastapi/uvicorn +
// libvirt, voir app/main.py) et GET /host/metrics/history (collecte
// continue, voir app/core/metrics.py -- chantier 10). Remplace l'ancienne
// version marquee "Mock uniquement" (noyau/version Python codes en dur,
// jamais mis a jour) -- trouve en auditant ce chantier.
export default function NodeSystemTab({ resource: node }) {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [latest, setLatest] = useState(null);

  useEffect(() => {
    fetch("/health").then((r) => r.json()).then(setHealth).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    const load = () => fetchHostMetricsHistory("1h").then((rows) => setLatest(rows[rows.length - 1] || null)).catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  if (!node) return null;

  const rows = health ? [
    ["Noyau", health.kernel],
    ["Hyperviseur", `${health.hypervisor} via libvirt ${health.libvirt_version}`],
    ["Nom d'hôte libvirt", health.hostname],
    ["Python / FastAPI / uvicorn", `${health.python_version} / ${health.fastapi_version} / ${health.uvicorn_version ?? "--"}`],
  ] : [];

  return (
    <div className="space-y-4">
      <div className="card divide-y divide-anthracite-600">
        {error && <div className="px-4 py-3 text-sm text-status-error">Erreur : {error}</div>}
        {!health && !error && <div className="px-4 py-3 text-sm text-anthracite-400">Chargement...</div>}
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between px-4 py-3 text-sm">
            <span className="text-anthracite-300">{label}</span>
            <span className="text-anthracite-100 font-mono">{value}</span>
          </div>
        ))}
      </div>

      {latest && (
        <div className="card grid grid-cols-2 gap-6 p-5">
          <GaugeRing label="CPU hôte" ratio={(latest.cpu_pct ?? 0) / 100} valueLabel={`${latest.cpu_pct ?? 0}%`} colorClass="text-accent-blue" />
          <GaugeRing
            label="RAM hôte"
            ratio={latest.mem_total_mb ? (latest.mem_used_mb ?? 0) / latest.mem_total_mb : 0}
            valueLabel={latest.mem_total_mb ? `${Math.round(latest.mem_used_mb)} / ${Math.round(latest.mem_total_mb)} Mo` : "--"}
            colorClass="text-accent-orange"
          />
        </div>
      )}

      <MetricsHistoryCard title="Historique CPU hôte (persisté)" fetcher={fetchHostMetricsHistory} />
    </div>
  );
}
