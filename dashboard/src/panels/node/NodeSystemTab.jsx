import LoadingState from "../../components/LoadingState";
import { useEffect, useState } from "react";
import GaugeRing from "../../components/GaugeRing";
import MetricsHistoryCard from "../../components/MetricsHistoryCard";
import { fetchHostMetricsHistory } from "../../api/client";
import { Card } from "@/components/ui/card";

// Real, from GET /health (platform/sys/fastapi/uvicorn introspection + libvirt,
// see app/main.py) and GET /host/metrics/history (continuous collection, see
// app/core/metrics.py). Replaces the former "Mock only" version (kernel/Python
// version hardcoded, never updated).
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
    ["Kernel", health.kernel],
    ["Hypervisor", `${health.hypervisor} via libvirt ${health.libvirt_version}`],
    ["libvirt hostname", health.hostname],
    ["Python / FastAPI / uvicorn", `${health.python_version} / ${health.fastapi_version} / ${health.uvicorn_version ?? "--"}`],
  ] : [];

  return (
    <div className="space-y-4">
      <Card className="p-0 divide-y divide-border">
        {error && <div className="px-4 py-3 text-sm text-status-error">Error: {error}</div>}
        {!health && !error && <div className="px-4 py-3 text-sm text-muted-foreground"><LoadingState /></div>}
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between px-4 py-3 text-sm transition-colors duration-150 hover:bg-muted/40">
            <span className="text-foreground/90">{label}</span>
            <span className="text-foreground font-mono">{value}</span>
          </div>
        ))}
      </Card>

      {latest && (
        <Card className="grid grid-cols-2 gap-6 p-5">
          <GaugeRing label="Host CPU" ratio={(latest.cpu_pct ?? 0) / 100} valueLabel={`${latest.cpu_pct ?? 0}%`} colorClass="text-accent-blue" />
          <GaugeRing
            label="Host RAM"
            ratio={latest.mem_total_mb ? (latest.mem_used_mb ?? 0) / latest.mem_total_mb : 0}
            valueLabel={latest.mem_total_mb ? `${Math.round(latest.mem_used_mb)} / ${Math.round(latest.mem_total_mb)} MB` : "--"}
            colorClass="text-accent-orange"
          />
        </Card>
      )}

      <MetricsHistoryCard title="Host CPU history (persisted)" fetcher={fetchHostMetricsHistory} />
    </div>
  );
}
