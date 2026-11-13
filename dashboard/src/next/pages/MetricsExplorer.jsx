import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Download, Pin, Plus, RefreshCw, X, Clock } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { AGGREGATIONS, METRICS, RANGES, canAdd, seriesKey, stats, toCsvRows } from "../lib/metricsExplorer";
import { useExplorerStore, useSeriesData } from "../lib/useExplorer";
import ExplorerChart, { useSeriesLabel } from "../components/ExplorerChart";
import { PageHeader, TableWrap } from "../components/ui";
import { Cmd, PickCmd, downloadCsv } from "../components/portal";

const vmKey = (v) => (v.node && v.node !== "local" ? `${v.name}@${v.node}` : v.name);

// The chart shown when nothing was explored yet: the processor of every online node.
function defaultChart(nodes) {
  return { range: "24h", series: nodes.filter((n) => n.state === "online").slice(0, 6).map((n) => ({ scope: { type: "node", id: n.id }, metric: "cpu", agg: "avg" })) };
}

// Metrics explorer: any metric of any node or VM, several on one chart, over a range, with an aggregation;
// a chart can be pinned to Home.
export default function MetricsExplorer() {
  const t = useT();
  const { nodes, vms, pushToast } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, pushToast: s.pushToast })));
  const { stored, setChart, pin } = useExplorerStore(useShallow((s) => ({ stored: s.chart, setChart: s.setChart, pin: s.pin })));
  const chart = stored || defaultChart(nodes);
  const { reload } = useSeriesData(chart.series, chart.range);
  const labelOf = useSeriesLabel();
  const scopes = [
    ...nodes.map((n) => ({ value: `node:${n.id}`, label: `${t("ns.node")} · ${n.name}`, scope: { type: "node", id: n.id } })),
    ...vms.filter((v) => !v.template).map((v) => ({ value: `vm:${vmKey(v)}`, label: `VM · ${v.name}${v.node && v.node !== "local" ? ` (${nodes.find((n) => n.id === v.node)?.name || v.node})` : ""}`, scope: { type: "vm", id: vmKey(v), name: v.name, node: v.node } })),
  ];
  const [scopeV, setScopeV] = useState("");
  const [metric, setMetric] = useState("cpu");
  const [agg, setAgg] = useState("avg");
  const picked = scopes.find((s) => s.value === (scopeV || scopes[0]?.value));
  const next = picked && { scope: picked.scope, metric, agg };
  const why = next ? canAdd(chart.series, next) : "scope";
  const update = (patch) => setChart({ ...chart, ...patch });
  const add = () => { if (!why) update({ series: [...chart.series, next] }); };
  const remove = (key) => update({ series: chart.series.filter((s) => seriesKey(s) !== key) });
  const title = chart.series.length === 1 ? labelOf(chart.series[0]) : t("mxp.chartTitle", { n: chart.series.length });
  const doPin = () => { pin(chart, title); pushToast({ kind: "success", title: t("mxp.pinned"), message: title }); };

  return (
    <>
      <PageHeader title={t("tab.explorer")} desc={t("mxp.desc")} actions={(
        <>
          <Cmd icon={Pin} label={t("mxp.pin")} onClick={doPin} disabled={chart.series.length === 0} />
          <Cmd icon={RefreshCw} label={t("action.refresh")} onClick={reload} />
          <ExportCmd chart={chart} labelOf={labelOf} />
          <PickCmd icon={Clock} label={t("mxp.range")} value={chart.range} onChange={(range) => update({ range })} options={RANGES.map((r) => ({ value: r, label: t(`mxp.r.${r}`) }))} />
        </>
      )} />
      <div className="nx-card2 hc-mx">
        <div className="hc-mx-add" role="group" aria-label={t("mxp.addMetric")}>
          <label className="hc-mx-f"><span>{t("mxp.scope")}</span>
            <select className="nx-inp" value={picked?.value || ""} onChange={(e) => setScopeV(e.target.value)}>
              <optgroup label={t("tab.nodes")}>{scopes.filter((s) => s.scope.type === "node").map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</optgroup>
              <optgroup label={t("nav.vms")}>{scopes.filter((s) => s.scope.type === "vm").map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</optgroup>
            </select></label>
          <label className="hc-mx-f"><span>{t("mxp.metric")}</span>
            <select className="nx-inp" value={metric} onChange={(e) => setMetric(e.target.value)}>{Object.keys(METRICS).map((m) => <option key={m} value={m}>{t(`mxp.m.${m}`)}</option>)}</select></label>
          <label className="hc-mx-f"><span>{t("mxp.aggregation")}</span>
            <select className="nx-inp" value={agg} onChange={(e) => setAgg(e.target.value)}>{AGGREGATIONS.map((a) => <option key={a} value={a}>{t(`mxp.agg.${a}`)}</option>)}</select></label>
          <button type="button" className="nx-btn nx-btn--primary" onClick={add} aria-disabled={!!why || undefined} aria-describedby={why ? "hc-mx-why" : undefined}><Plus size={16} aria-hidden="true" />{t("mxp.add")}</button>
          {why && why !== "scope" && <p id="hc-mx-why" className="nx-hint">{t(`mxp.why.${why}`)}</p>}
        </div>
        {chart.series.length > 0 && (
          <ul className="hc-mx-chips" aria-label={t("mxp.series")}>
            {chart.series.map((s, i) => (
              <li key={seriesKey(s)}><i data-tone={`s${(i % 6) + 1}`} aria-hidden="true" />{labelOf(s)}
                <button type="button" className="hc-ibtn" aria-label={t("mxp.remove", { name: labelOf(s) })} title={t("mxp.removeShort")} onClick={() => remove(seriesKey(s))}><X size={14} aria-hidden="true" /></button></li>
            ))}
          </ul>
        )}
        <ExplorerChart chart={chart} height={300} label={title}>
          {(drawn, fmt) => drawn.length > 0 && (
            <TableWrap>
              <table className="nx-table hc-grid--list hc-mx-stats">
                <thead><tr><th scope="col">{t("mxp.series1")}</th><th scope="col" className="nx-num">{t("mxp.min")}</th><th scope="col" className="nx-num">{t("mxp.agg.avg")}</th><th scope="col" className="nx-num">{t("mxp.max")}</th><th scope="col" className="nx-num">{t("mxp.last")}</th></tr></thead>
                <tbody>{drawn.map((s) => { const st = stats(s.points); return (
                  <tr key={s.key}><th scope="row"><span className="hc-mx-sw"><i data-tone={s.tone} aria-hidden="true" />{s.label}</span></th>
                    {st ? ["min", "avg", "max", "last"].map((k) => <td key={k} className="nx-num nx-mono">{fmt(st[k])}</td>) : <td colSpan={4} className="nx-muted">{s.failed ? t("vp.failed") : t("mxp.noData")}</td>}</tr>
                ); })}</tbody>
              </table>
            </TableWrap>
          )}
        </ExplorerChart>
      </div>
      <p className="nx-hint">{t("mxp.note")}</p>
    </>
  );
}
MetricsExplorer.ownHeader = true;

function ExportCmd({ chart, labelOf }) {
  const t = useT();
  const { data } = useSeriesData(chart.series, chart.range);
  const series = chart.series.map((s) => ({ points: Array.isArray(data?.[seriesKey(s)]) ? data[seriesKey(s)] : [] }));
  const empty = series.every((s) => !s.points.length);
  return <Cmd icon={Download} label={t("list.exportCsv")} disabled={empty} onClick={() => downloadCsv("metrics.csv", toCsvRows(series, [t("mxp.time"), ...chart.series.map(labelOf)]))} />;
}
