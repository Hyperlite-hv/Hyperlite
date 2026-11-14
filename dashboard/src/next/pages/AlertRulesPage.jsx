import { useCached } from "../lib/useCached";
import { Fragment, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { BellPlus, ChevronDown, ChevronRight, Pencil, RefreshCw, Siren, Trash2 } from "lucide-react";
import { createAlertRule, deleteAlertRule, fetchAlertRules, fetchNotificationChannels, setAlertRuleEnabled, updateAlertRule } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { usePolling } from "../lib/polling";
import { PageHeader, SideDrawer, Field, Empty, TableWrap } from "../components/ui";
import { Cmd } from "../components/portal";
import { InlineError } from "../components/States";

export const METRICS = ["cpu_pct", "mem_pct", "disk_read_bps", "disk_write_bps", "net_rx_bps", "net_tx_bps"];
const PCT = new Set(["cpu_pct", "mem_pct"]);
const WINDOWS = [1, 5, 10, 15, 30, 60];
const SEVERITIES = ["critical", "warning", "info"];
const MB = 1024 * 1024; // throughputs are typed in MB/s and stored in bytes per second
const asList = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []);
const EMPTY = { name: "", scope: "all_vms", node: "", vm: "", metric: "cpu_pct", operator: "gt", threshold: "85", window_min: 5, severity: "warning", channels: [], enabled: true };

// "Processor > 85 % for 10 min"
export function conditionText(r, t, lang) {
  const nf = new Intl.NumberFormat(lang, { maximumFractionDigits: 1 });
  const value = PCT.has(r.metric) ? `${nf.format(r.threshold)} %` : `${nf.format(r.threshold / MB)} ${t("al.mbps")}`;
  return t("al.condition", { metric: t(`al.m.${r.metric}`), op: r.operator === "gt" ? ">" : "<", value, n: r.window_min });
}

function scopeText(r, t, nodes) {
  const nodeName = (id) => nodes.find((n) => n.id === id)?.name || id;
  if (r.scope === "all_vms") return t("al.scope.all_vms");
  if (r.scope === "all_nodes") return t("al.scope.all_nodes");
  if (r.scope === "node") return t("al.onNode", { node: nodeName(r.node) });
  return t("al.onVm", { vm: r.vm, node: nodeName(r.node) });
}

function RuleDrawer({ open, rule, channels, onClose, onSaved }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, vms, pushToast } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, pushToast: s.pushToast })));
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!open) return;
    setTried(false); setError(null);
    setForm(rule ? { ...EMPTY, ...rule, node: rule.node || "", vm: rule.vm || "", threshold: String(PCT.has(rule.metric) ? rule.threshold : rule.threshold / MB) } : { ...EMPTY, node: nodes[0]?.id || "" });
  }, [open, rule]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const pct = PCT.has(form.metric);
  const num = Number(String(form.threshold).replace(",", "."));
  const errors = {
    name: !form.name.trim() ? t("al.e.name") : null,
    threshold: !Number.isFinite(num) || num < 0 || (pct && num > 100) ? t(pct ? "al.e.pct" : "al.e.rate") : null,
    vm: form.scope === "vm" && !form.vm ? t("al.e.vm") : null,
  };
  const err = (k) => (tried ? errors[k] : null);
  const nodeVms = vms.filter((v) => !v.template && (v.node || "local") === form.node);
  const payload = () => ({
    name: form.name.trim(), enabled: form.enabled, scope: form.scope,
    node: form.scope === "node" || form.scope === "vm" ? form.node : null, vm: form.scope === "vm" ? form.vm : null,
    metric: form.metric, operator: form.operator, threshold: pct ? num : num * MB, window_min: Number(form.window_min),
    severity: form.severity, channels: form.channels.map(Number),
  });
  async function save() {
    setTried(true);
    if (Object.values(errors).some(Boolean)) return;
    setBusy(true); setError(null);
    try {
      const saved = rule ? await updateAlertRule(rule.id, payload()) : await createAlertRule(payload());
      pushToast({ kind: "success", title: rule ? t("al.saved") : t("al.created"), message: saved.name });
      onSaved(); onClose();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  const toggleChannel = (id) => setForm((f) => ({ ...f, channels: f.channels.includes(id) ? f.channels.filter((c) => c !== id) : [...f.channels, id] }));
  return (
    <SideDrawer open={open} title={rule ? t("al.edit", { name: rule.name }) : t("al.create")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={save}>{rule ? t("al.save") : t("al.create")}</button>
    </>}>
      {error && <InlineError message={error} />}
      <Field label={t("al.name")} error={err("name")}>{(p) => <input {...p} className="nx-inp" value={form.name} onChange={set("name")} maxLength={80} placeholder={t("al.namePh")} />}</Field>
      <h3 className="hc-al-h">{t("al.watched")}</h3>
      <Field label={t("al.scope")}>{(p) => <select {...p} className="nx-inp" value={form.scope} onChange={set("scope")}>{["all_vms", "vm", "all_nodes", "node"].map((s) => <option key={s} value={s}>{t(`al.scope.${s}`)}</option>)}</select>}</Field>
      {(form.scope === "node" || form.scope === "vm") && <Field label={t("ns.node")}>{(p) => <select {...p} className="nx-inp" value={form.node} onChange={(e) => setForm((f) => ({ ...f, node: e.target.value, vm: "" }))}>{nodes.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}{form.node && !nodes.some((n) => n.id === form.node) && <option value={form.node}>{form.node}</option>}</select>}</Field>}
      {form.scope === "vm" && <Field label="VM" error={err("vm")}>{(p) => <select {...p} className="nx-inp" value={form.vm} onChange={set("vm")}><option value="">{t("al.pickVm")}</option>{nodeVms.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}{form.vm && !nodeVms.some((v) => v.name === form.vm) && <option value={form.vm}>{form.vm}</option>}</select>}</Field>}
      <h3 className="hc-al-h">{t("al.conditionH")}</h3>
      <Field label={t("al.metric")}>{(p) => <select {...p} className="nx-inp" value={form.metric} onChange={set("metric")}>{METRICS.map((m) => <option key={m} value={m}>{t(`al.m.${m}`)}</option>)}</select>}</Field>
      <div className="hc-al-row">
        <Field label={t("al.operator")}>{(p) => <select {...p} className="nx-inp" value={form.operator} onChange={set("operator")}><option value="gt">{t("al.op.gt")}</option><option value="lt">{t("al.op.lt")}</option></select>}</Field>
        <Field label={t("al.threshold")} unit={pct ? "%" : t("al.mbps")} error={err("threshold")}>{(p) => <input {...p} className="nx-inp" inputMode="decimal" value={form.threshold} onChange={set("threshold")} />}</Field>
      </div>
      <Field label={t("al.window")} hint={t("al.windowHelp")}>{(p) => <select {...p} className="nx-inp" value={form.window_min} onChange={set("window_min")}>{WINDOWS.map((w) => <option key={w} value={w}>{t("al.minutes", { n: w })}</option>)}</select>}</Field>
      <h3 className="hc-al-h">{t("al.actions")}</h3>
      <Field label={t("al.severity")}>{(p) => <select {...p} className="nx-inp" value={form.severity} onChange={set("severity")}>{SEVERITIES.map((s) => <option key={s} value={s}>{t(`al.sev.${s}`)}</option>)}</select>}</Field>
      <fieldset className="hc-al-ch">
        <legend>{t("al.channels")}</legend>
        {channels == null ? <p className="nx-hint">{t("loading")}</p> : channels.length === 0 ? <p className="nx-hint">{t("al.noChannel")}</p> : channels.map((c) => (
          <label key={c.id} className="nx-check"><input type="checkbox" checked={form.channels.includes(c.id)} onChange={() => toggleChannel(c.id)} />{c.name}<span className="nx-muted"> · {c.type}</span></label>
        ))}
        <p className="nx-hint">{t("al.channelsHelp")}</p>
      </fieldset>
      <label className="nx-check"><input type="checkbox" checked={form.enabled} onChange={set("enabled")} />{t("al.enabled")}</label>
      {!Object.values(errors).some(Boolean) && <p className="hc-al-preview" role="status"><b>{t("al.preview")}</b> {t("al.previewText", { scope: scopeText(payload(), t, nodes), cond: conditionText(payload(), t, lang) })}</p>}
    </SideDrawer>
  );
}

// Alert rules: what an administrator wants to be told about, and which rules fire now.
export default function AlertRulesPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, pushToast } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, pushToast: s.pushToast })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const cached = useCached("alert-rules", async () => asList(await fetchAlertRules()));
  const rules = cached.data;
  const error = cached.error ? errorMessage(cached.error) : null;
  const [channels, setChannels] = useState(null);
  const [drawer, setDrawer] = useState(null); // { rule } | { rule: null } for a new one
  const [open, setOpen] = useState(null);
  const load = cached.reload;
  usePolling(load, 30000);
  useEffect(() => { if (caps.admin) fetchNotificationChannels().then((r) => setChannels(asList(r))).catch(() => setChannels([])); }, [caps.admin]);
  const channelName = (id) => channels?.find((c) => c.id === id)?.name || `#${id}`;

  async function toggle(r) {
    try { await setAlertRuleEnabled(r.id, !r.enabled); load(); } catch (e) { pushToast({ kind: "error", title: t("al.saveFailed"), message: errorMessage(e) }); }
  }
  async function remove(r) {
    if (!(await confirmAction({ title: t("al.deleteTitle", { name: r.name }), message: t("al.deleteMsg"), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    try { await deleteAlertRule(r.id); pushToast({ kind: "success", title: t("al.deleted"), message: r.name }); load(); } catch (e) { pushToast({ kind: "error", title: t("al.deleteFailed"), message: errorMessage(e) }); }
  }
  const firing = (rules || []).filter((r) => r.enabled && r.firing.length);

  return (
    <>
      <PageHeader title={t("tab.alerts")} desc={t("al.desc")} actions={(
        <>
          {caps.admin && <Cmd icon={BellPlus} label={t("al.create")} onClick={() => setDrawer({ rule: null })} />}
          <Cmd icon={RefreshCw} label={t("action.refresh")} onClick={load} />
        </>
      )} />
      {error && <InlineError message={error} onRetry={load} />}
      {rules && rules.length > 0 && <p className="hc-count" role="status">{firing.length ? t((lang === "fr" ? firing.length < 2 : firing.length === 1) ? "al.nFiring.one" : "al.nFiring", { n: firing.length, total: rules.length }) : t(rules.length === 1 ? "al.noneFiring.one" : "al.noneFiring", { total: rules.length })}</p>}
      <div className="nx-card2 nx-card2--flush">
        {rules == null ? <p className="nx-muted" style={{ padding: "var(--space-4)" }}>{t("loading")}</p>
          : rules.length === 0 ? <Empty icon={Siren} title={t("al.none")} text={t("al.noneHelp")} action={caps.admin ? <button type="button" className="nx-btn nx-btn--primary" onClick={() => setDrawer({ rule: null })}>{t("al.create")}</button> : null} />
            : (
              <TableWrap>
                <table className="nx-table hc-grid--list">
                  <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("al.name")}</th><th scope="col">{t("al.conditionH")}</th><th scope="col">{t("al.scope")}</th><th scope="col">{t("al.severity")}</th><th scope="col">{t("al.channels")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                  <tbody>
                    {rules.map((r) => {
                      const state = !r.enabled ? "off" : r.firing.length ? "firing" : "ok";
                      return (
                        <Fragment key={r.id}>
                          <tr>
                            <td><span className="hc-al-st" data-state={state}>{t(`al.st.${state}`)}{state === "firing" && r.firing.length > 1 ? ` (${r.firing.length})` : ""}</span></td>
                            <th scope="row" className="hc-al-t">
                              <button type="button" className="hc-al-open" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
                                {open === r.id ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}<span>{r.name}</span>
                              </button>
                            </th>
                            <td>{conditionText(r, t, lang)}</td>
                            <td>{scopeText(r, t, nodes)}</td>
                            <td><span className="hc-sev" data-level={r.severity === "critical" ? "high" : r.severity === "warning" ? "medium" : "low"}>{t(`al.sev.${r.severity}`)}</span></td>
                            <td className="nx-wrapcell">{r.channels.length ? r.channels.map(channelName).join(", ") : <span title={t("al.allChannels")}>{t("al.allChannelsShort")}</span>}</td>
                            <td>{caps.admin && <div className="nx-ra">
                              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => toggle(r)}>{r.enabled ? t("al.disable") : t("al.enable")}</button>
                              <button type="button" className="hc-ibtn" aria-label={t("al.edit", { name: r.name })} title={t("al.editShort")} onClick={() => setDrawer({ rule: r })}><Pencil size={15} aria-hidden="true" /></button>
                              <button type="button" className="hc-ibtn" aria-label={t("al.deleteTitle", { name: r.name })} title={t("menu.delete").replace("…", "")} onClick={() => remove(r)}><Trash2 size={15} aria-hidden="true" /></button>
                            </div>}</td>
                          </tr>
                          {open === r.id && (
                            <tr><td colSpan={7} className="nx-detailcell">
                              <div className="hc-al-d">
                                <div><h3>{t("al.firingFor")}</h3>{r.firing_labels?.length ? <ul className="hc-al-res">{r.firing_labels.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="nx-muted">{t("al.nothingFiring")}</p>}</div>
                                <div><h3>{t("al.lastChange")}</h3><p>{r.changed_at ? formatDateTime(r.changed_at, lang) : "—"}</p></div>
                                <div><h3>{t("al.createdBy")}</h3><p>{r.created_by || "—"}{r.created_at ? ` · ${formatDateTime(r.created_at, lang)}` : ""}</p></div>
                              </div>
                            </td></tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </TableWrap>
            )}
      </div>
      <RuleDrawer open={!!drawer} rule={drawer?.rule || null} channels={channels} onClose={() => setDrawer(null)} onSaved={load} />
    </>
  );
}
AlertRulesPage.ownHeader = true;
