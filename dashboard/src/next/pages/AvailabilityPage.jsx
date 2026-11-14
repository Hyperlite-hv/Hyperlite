import { useCallback, useEffect, useState } from "react";
import { Activity, Pin } from "lucide-react";
import { fetchAvailability } from "../../api/client";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatUptimeLong, relativeTime } from "../lib/format";
import { Card, Chip, Empty, KpiStrip, Pill, TableWrap } from "../components/ui";
import { InlineError } from "../components/States";
import { PinDialog } from "../components/PinDialog";

const RANGES = [1, 7, 30];
// The tone of each state, as everywhere else in the console.
export const STATE_TONE = { running: "success", paused: "info", stopped: "offline", crashed: "danger", unreachable: "danger", unknown: "warning" };
// A libvirt or Hyperlite reason in words; one this version does not know is shown as it is.
export const reasonText = (t, r) => { const k = `av.r.${String(r).replace(/ /g, "-")}`; const v = t(k); return v === k ? r : v; };
export const pct = (v) => (v == null ? "—" : `${v >= 99.995 ? "100" : v.toFixed(v >= 99 ? 2 : 1)}`);

// The states over the period as one bar: each period's width is its share of the time.
export function AvailabilityStrip({ strip, label }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  if (!strip?.length) return null;
  const a = Date.parse(strip[0].start);
  const total = Math.max(1, Date.parse(strip[strip.length - 1].end) - a);
  return (
    <div className="hc-av-strip" role="img" aria-label={label}>
      {strip.map((p, i) => {
        const w = ((Date.parse(p.end) - Date.parse(p.start)) / total) * 100;
        return <span key={i} data-tone={STATE_TONE[p.state] || "offline"} style={{ width: `${w}%` }}
          title={`${t(`av.s.${p.state}`)} · ${formatDateTime(p.start, lang)} → ${formatDateTime(p.end, lang)}`} />;
      })}
    </div>
  );
}

function Availability({ kind, name, node, title }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [days, setDays] = useState(7);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [pinning, setPinning] = useState(false);
  const load = useCallback(() => fetchAvailability(kind, name, node, days).then((r) => { setData(r); setError(null); }).catch((e) => setError(errorMessage(e))), [kind, name, node, days]);
  useEffect(() => { load(); const id = setInterval(load, 60000); return () => clearInterval(id); }, [load]);
  if (error) return <InlineError message={error} />;
  if (!data) return <p className="nx-muted">{t("loading")}</p>;
  const now = data.now;
  const cause = (e) => {
    if (e.by) return t("av.by", { who: e.by.username, what: t(`av.a.${e.by.action}`) });
    return e.reason ? reasonText(t, e.reason) : "—";
  };
  const w = data.windows;
  return (
    <>
      <div className="hc-av-head">
        <Pill tone={STATE_TONE[now.state] || "offline"}>{t(`av.state.${now.state}`)}</Pill>
        <span className="nx-muted">{now.reason && now.state !== "running" ? reasonText(t, now.reason) : ""}{now.since ? ` · ${t("av.since", { when: relativeTime(Date.parse(now.since), lang) })}` : ""}</span>
        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setPinning(true)}><Pin size={14} aria-hidden="true" /> {t("pd.action")}</button>
      </div>
      <p className="nx-ph-desc">{t("av.desc")}</p>
      <KpiStrip label={t("av.kpis")} items={RANGES.map((d) => ({
        id: String(d), label: t(`av.range.${d}`), value: pct(w[String(d)]?.available_pct), unit: w[String(d)]?.available_pct == null ? null : "%",
        sub: w[String(d)] ? t("av.cuts", { n: w[String(d)].interruptions }) : null, subTone: w[String(d)]?.interruptions ? "warning" : undefined,
      }))} />
      <Card title={t("av.over")} actions={<div className="hc-seg" role="group" aria-label={t("av.period")}>{RANGES.map((d) => <button key={d} type="button" className="nx-btn nx-btn--sm" aria-pressed={days === d} onClick={() => setDays(d)}>{t(`av.range.${d}`)}</button>)}</div>}>
        <AvailabilityStrip strip={data.strip} label={t("av.stripLabel", { name: title })} />
        <div className="hc-av-legend">{["running", "stopped", "paused", "crashed", "unknown"].map((s) => <span key={s}><i data-tone={STATE_TONE[s]} aria-hidden="true" />{t(`av.s.${s}`)}</span>)}</div>
        <p className="nx-muted" style={{ margin: 0 }}>{t("av.totals", { up: formatUptimeLong(w[String(days)]?.running_s, lang) || "0", planned: formatUptimeLong(w[String(days)]?.planned_s, lang) || "0", down: formatUptimeLong(w[String(days)]?.unplanned_s, lang) || "0" })}</p>
      </Card>
      <Card title={t("av.events")} flush>
        {!data.events.length ? <Empty icon={Activity} title={t("av.noEvents")} text={t("av.noEventsText")} /> : (
          <TableWrap label={t("av.events")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("av.when")}</th><th scope="col">{t("av.length")}</th><th scope="col">{t("av.what")}</th><th scope="col">{t("av.cause")}</th></tr></thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={e.start}>
                    <td className="nx-mono">{formatDateTime(e.start, lang)}</td>
                    <td className="nx-mono">{formatUptimeLong(e.seconds, lang) || t("av.lessMin")}</td>
                    <td><Chip tone={e.kind === "unplanned" ? "danger" : undefined}>{t(`av.s.${e.state}`)}</Chip> {e.kind === "unplanned" && <span className="nx-tone-danger">{t("av.unplanned")}</span>}</td>
                    <td>{cause(e)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <PinDialog open={pinning} onClose={() => setPinning(false)} tile={{ type: "availability", title: t("av.tileTitle", { name: title }), size: 1, config: { items: [{ kind, name, node: node || null }] } }} />
    </>
  );
}

// VM ▸ Availability.
export default function VmAvailabilityPage({ resource: vm }) {
  return <Availability kind="vm" name={vm.name} node={vm.node} title={vm.name} />;
}

// Node ▸ Availability.
export function NodeAvailabilityPage({ resource: node }) {
  return <Availability kind="node" name={node.id} node={null} title={node.name} />;
}
