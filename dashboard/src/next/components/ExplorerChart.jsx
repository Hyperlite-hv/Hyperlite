import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { formatRate, formatSizeMb } from "../lib/format";
import { METRICS, seriesKey, unitOf } from "../lib/metricsExplorer";
import { useSeriesData } from "../lib/useExplorer";
import LineChart from "./LineChart";

const TONES = ["s1", "s2", "s3", "s4", "s5", "s6"];

// The name of a series: where, what, how ("hv-01 · Processor (avg)").
export function useSeriesLabel() {
  const t = useT();
  const nodes = useInfraStore((s) => s.nodes);
  return (s) => {
    const where = s.scope.type === "node" ? nodes.find((n) => n.id === s.scope.id)?.name || s.scope.id : s.scope.name || s.scope.id;
    return `${where} · ${t(`mxp.m.${s.metric}`)} (${t(`mxp.agg.${s.agg}`)})`;
  };
}

export function unitFormat(unit, lang) {
  const nf = (v) => new Intl.NumberFormat(lang, { maximumFractionDigits: v < 10 ? 1 : 0 }).format(v);
  if (unit === "pct") return (v) => `${nf(v)} %`;
  if (unit === "mb") return (v) => formatSizeMb(v, lang) ?? "—";
  return (v, compact) => formatRate(v, lang, compact);
}
export const timeFormat = (range, lang) => (tk) => new Date(tk).toLocaleString(lang, range === "7j" || range === "30j" ? { day: "2-digit", month: "2-digit" } : { hour: "2-digit", minute: "2-digit" });

// A chart of the explorer (or a pinned one): every series on one scale. `render` receives the drawn series too, so the
// page can list their statistics under it.
export default function ExplorerChart({ chart, height = 260, label, children }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { data } = useSeriesData(chart.series, chart.range);
  const labelOf = useSeriesLabel();
  useInfraStore(useShallow((s) => s.nodes.length)); // relabel once the inventory is in
  const unit = unitOf(chart.series);
  const fmt = unitFormat(unit, lang);
  const drawn = chart.series.map((s, i) => ({ key: seriesKey(s), label: labelOf(s), tone: TONES[i % TONES.length], points: Array.isArray(data?.[seriesKey(s)]) ? data[seriesKey(s)] : [], failed: data?.[seriesKey(s)] === "error", def: s }));
  const empty = !chart.series.length ? t("mxp.noSeries") : data == null ? t("loading") : drawn.every((s) => s.points.length < 2) ? (drawn.some((s) => s.failed) ? t("vp.failed") : t("mxp.noData")) : null;
  return (
    <>
      {empty ? <p className="nx-vm-chart-empty nx-muted" style={{ minHeight: `${Math.round(height * 0.6)}px` }}>{empty}</p>
        : <LineChart series={drawn} label={label || drawn.map((s) => s.label).join(", ")} formatTime={timeFormat(chart.range, lang)} height={height} max={unit === "pct" ? 100 : "auto"} base={unit === "bps" ? 1024 : 10} format={fmt} />}
      {children?.(drawn, fmt)}
    </>
  );
}

export const metricUnit = (metric) => METRICS[metric]?.unit;
