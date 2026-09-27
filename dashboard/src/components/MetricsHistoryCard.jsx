import { useEffect, useState } from "react";
import MetricChart from "./MetricChart";
import { chartColors } from "../theme/colors";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const RANGES = [
  { key: "1h", label: "1 h" },
  { key: "24h", label: "24 h" },
  { key: "7j", label: "Week" },
  { key: "30j", label: "Month" },
];

// Real: GET /vms/{name}/metrics/history or /host/metrics/history (see
// app/routers/metrics.py): history PERSISTED on the backend (1h = raw samples
// every 15s, 24h/week/month = hourly averages), as opposed to the "current
// session" graph of useLiveVMMetrics, which only keeps the last ~8 minutes in
// browser memory.
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
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <Tabs value={range} onValueChange={setRange}>
          <TabsList>
            {RANGES.map((r) => <TabsTrigger aria-controls={undefined} key={r.key} value={r.key}>{r.label}</TabsTrigger>)}
          </TabsList>
        </Tabs>
      </div>
      {error && <p className="mt-2 text-xs text-status-error">{error}</p>}
      {rows != null && rows.length === 0 && <p className="mt-3 text-sm text-muted-foreground">Not enough history yet for this period.</p>}
      {rows != null && rows.length > 0 && (
        <div className="mt-3">
          <MetricChart data={data} series={[{ key: "cpu", label: "CPU", color: chartColors.cpu }]} yFormatter={(v) => `${Math.round(v * 100)}%`} height={140} />
        </div>
      )}
    </Card>
  );
}
