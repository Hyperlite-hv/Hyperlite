import { useCallback, useEffect, useState } from "react";
import { HeartPulse, ShieldAlert } from "lucide-react";
import { fetchHaHistory, setHaAdmission, setHaMonitoring, setHaPolicy } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Card, Field, SideDrawer, TableWrap } from "./ui";
import { InlineError } from "./States";

// Automatic HA in a cluster (docs/design/ha-automatic.md, #333 step 7): the leader restarts the HA VMs of a lost
// member once it is fenced. What the page shows of it: the banner, each member's state, the admission control
// policy, the HA history, and each VM's restart policy and monitoring.

export const STATE_TONE = { ok: "success", lost: "danger", fencing: "warning", recovering: "warning", manual: "danger", suspect: "warning", down: "danger", libvirt_unreachable: "warning" };

export function AutomaticBanner({ status }) {
  const t = useT();
  return (
    <div className="nx-bn" data-tone="success" role="status"><HeartPulse size={16} aria-hidden="true" />
      <span className="nx-bn-t"><b>{t("ha.auto")}</b> {t("ha.autoHelp")}{status.leading && <><br />{t("ha.leading")}</>}</span>
    </div>
  );
}

function watchdog(t, w) {
  if (!w) return <span className="nx-muted">—</span>;
  if (w.stopped) return <span className="nx-chip" data-tone="warning">{t("ha.wd.stopped")}</span>;
  if (w.armed) return <span className="nx-chip" data-tone="success">{w.reset_within_s ? t("ha.wd.armedIn", { s: w.reset_within_s }) : t("ha.wd.armed")}</span>;
  return <span className="nx-muted">{t("ha.wd.off")}</span>;
}

export function MembersCard({ members }) {
  const t = useT();
  return (
    <Card title={t("ha.members")} flush>
      <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("ha.membersHelp")}</p>
      <TableWrap>
        <table className="nx-table">
          <thead><tr><th scope="col">{t("ha.node")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ha.watchdog")}</th><th scope="col">{t("ha.freeMem")}</th></tr></thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.name}>
                <th scope="row" className="nx-mono">{m.name}{m.self && <span className="nx-muted"> · {t("ha.thisNode")}</span>}</th>
                <td>{m.online ? <span className="nx-chip" data-tone="success">{t("ha.online")}</span> : <span className="nx-chip" data-tone="danger">{m.seen_s == null ? t("ha.unseen") : t("ha.lostFor", { s: Math.round(m.seen_s) })}</span>}</td>
                <td>{watchdog(t, m.watchdog)}</td>
                <td className="nx-mono">{m.mem_free_mb == null ? <span className="nx-muted">—</span> : t("ha.mib", { n: m.mem_free_mb })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  );
}

const POLICIES = ["warn", "strict", "off"];

export function AdmissionCard({ admission, editable, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);
  if (!admission) return null;
  async function change(policy) {
    setBusy(true);
    try { await setHaAdmission(policy); onSaved(); }
    catch (e) { pushToast({ kind: "error", title: t("ha.admissionFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  const unabsorbed = admission.unabsorbed || [];
  return (
    <Card title={t("ha.admission")}>
      <Field label={t("ha.policy")} hint={t(`ha.policy.${admission.policy}Help`)}>{(p) => (
        <select {...p} className="nx-inp" aria-label={t("ha.policy")} value={admission.policy} disabled={!editable || busy} onChange={(e) => change(e.target.value)}>
          {POLICIES.map((x) => <option key={x} value={x}>{t(`ha.policy.${x}`)}</option>)}
        </select>
      )}</Field>
      {admission.policy !== "off" && (unabsorbed.length ? (
        <div className="nx-bn" data-tone={admission.policy === "strict" ? "danger" : "warning"} role="status"><ShieldAlert size={16} aria-hidden="true" />
          <span className="nx-bn-t">{t("ha.unabsorbed", { nodes: unabsorbed.join(", ") })}</span></div>
      ) : <p className="nx-f-h">{(admission.unknown || []).length ? t("ha.admissionUnknown", { nodes: admission.unknown.join(", ") }) : t("ha.admissionOk")}</p>)}
    </Card>
  );
}

function eventLabel(t, action) {
  const label = t(`ha.ev.${action}`);
  return label === `ha.ev.${action}` ? action : label; // an event of a later version: its own name
}

export function HistoryCard() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const reload = useCallback(() => fetchHaHistory(50).then((r) => { setRows(Array.isArray(r) ? r : []); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { reload(); const id = setInterval(reload, 30000); return () => clearInterval(id); }, [reload]);
  const fmt = (iso) => new Date(iso).toLocaleString(lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return (
    <Card title={t("ha.history")} flush>
      {error ? <div style={{ margin: "0 var(--space-4) var(--space-3)" }}><InlineError message={error} onRetry={reload} /></div> : rows === null ? null : rows.length === 0 ? (
        <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("ha.historyNone")}</p>
      ) : (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("ha.when")}</th><th scope="col">{t("ha.event")}</th><th scope="col">{t("ha.object")}</th><th scope="col">{t("ha.detail")}</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="nx-mono">{fmt(r.timestamp)}</td>
                  <td><span className="nx-chip" data-tone={r.result === "success" ? "success" : "danger"}>{eventLabel(t, r.action)}</span></td>
                  <th scope="row" className="nx-mono">{r.resource}</th>
                  <td className="nx-wrapcell nx-f-h">{r.error_message || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

// An HA VM's restart priority, its preferred nodes (in order) and its monitoring.
export function PolicyDrawer({ row, nodes, onClose, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (row) setForm({ priority: row.priority || "medium", preferred: [...(row.preferred_nodes || [])], monitoring: !!row.monitoring });
  }, [row]);
  if (!row || !form) return <SideDrawer open={false} title="" onClose={onClose} />;
  const names = nodes.map((n) => n.name).filter(Boolean);
  const toggle = (name) => setForm((f) => ({ ...f, preferred: f.preferred.includes(name) ? f.preferred.filter((x) => x !== name) : [...f.preferred, name] }));
  async function save() {
    setBusy(true);
    try {
      await setHaPolicy(row.vm_name, form.priority, form.preferred);
      if (form.monitoring !== !!row.monitoring) await setHaMonitoring(row.vm_name, form.monitoring);
      pushToast({ kind: "success", title: t("ha.policySaved"), message: row.vm_name }); onSaved(); onClose();
    } catch (e) { pushToast({ kind: "error", title: t("ha.policyFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <SideDrawer open title={t("ha.policyOf", { vm: row.vm_name })} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={save}>{t("sso.save")}</button>
    </>}>
      <Field label={t("ha.priority")} hint={t("ha.priorityHelp")}>{(p) => (
        <select {...p} className="nx-inp" aria-label={t("ha.priority")} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
          {["high", "medium", "low"].map((x) => <option key={x} value={x}>{t(`ha.prio.${x}`)}</option>)}
        </select>
      )}</Field>
      <fieldset className="nx-stack" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="nx-f-l">{t("ha.preferred")}</legend>
        <p className="nx-f-h" style={{ margin: 0 }}>{t("ha.preferredHelp")}</p>
        {names.map((name) => {
          const at = form.preferred.indexOf(name);
          return <label key={name} className="nx-check"><input type="checkbox" checked={at >= 0} onChange={() => toggle(name)} /> <span className="nx-mono">{name}</span>{at >= 0 && <span className="nx-muted"> · {t("ha.rank", { n: at + 1 })}</span>}</label>;
        })}
      </fieldset>
      <div className="nx-inline" style={{ justifyContent: "space-between" }}>
        <span className="nx-f-l">{t("ha.monitoring")}</span>
        <button type="button" className="nx-sw" role="switch" aria-checked={form.monitoring} aria-label={t("ha.monitoring")} onClick={() => setForm({ ...form, monitoring: !form.monitoring })} />
      </div>
      <p className="nx-f-h" style={{ margin: 0 }}>{t("ha.monitoringHelp")}</p>
    </SideDrawer>
  );
}
