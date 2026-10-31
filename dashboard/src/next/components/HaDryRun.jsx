import { useCallback, useEffect, useState } from "react";
import { FlaskConical, ShieldCheck, Zap } from "lucide-react";
import { fetchHaStatus, saveHaSettings, fetchFencing, saveFencing, deleteFencing, testFencing, checkLeases } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Card, Field, SideDrawer, TableWrap } from "./ui";
import { InlineError } from "./States";
import { localizeApiText } from "../lib/apiText";

// Automatic HA in dry-run mode (docs/design/ha-automatic.md): the watcher's status, the witness and thresholds, and
// each node's fencing settings with a status-only test. Nothing here ever powers a node off or restarts a VM.
export function HaWatchBanner({ status }) {
  const t = useT();
  if (!status) return null;
  return (
    <div className="nx-bn" data-tone="info" role="status"><FlaskConical size={16} aria-hidden="true" />
      <span className="nx-bn-t"><b>{t("hw.dryRun")}</b> {t("hw.dryRunHelp")}{status.reason && <><br />{t("hw.autoOff", { reason: localizeApiText(t, status.reason) })}</>}</span>
    </div>
  );
}

export function useHaStatus() {
  const [status, setStatus] = useState(null);
  const reload = useCallback(() => fetchHaStatus().then(setStatus).catch(() => {}), []);
  useEffect(() => { reload(); const id = setInterval(reload, 15000); return () => clearInterval(id); }, [reload]);
  return [status, reload];
}

export function HaSettingsCard({ status, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (status && !form) setForm({ ...status.settings }); }, [status, form]);
  if (!form) return null;
  const s = Number(form.suspect_threshold), p = Number(form.failure_threshold);
  const bad = !Number.isInteger(s) || !Number.isInteger(p) || s < 1 || s >= p || p > 60;
  async function save(e) {
    e.preventDefault();
    if (bad) return;
    setBusy(true);
    try { const r = await saveHaSettings({ witness: form.witness.trim(), suspect_threshold: s, failure_threshold: p }); setForm({ ...r }); pushToast({ kind: "success", title: t("hw.saved") }); onSaved(); }
    catch (er) { pushToast({ kind: "error", title: t("hw.saveFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("hw.settings")}>
      <form onSubmit={save} className="nx-fg nx-fg--3">
        <Field label={t("hw.witness")} hint={t("hw.witnessHelp")}>{(pp) => <input {...pp} className="nx-inp nx-mono" aria-label={t("hw.witness")} placeholder="nas.example.lan" value={form.witness} onChange={(e) => setForm({ ...form, witness: e.target.value })} />}</Field>
        <Field label={t("hw.suspect")} unit={t("hw.rounds")} error={bad ? t("hw.thresholdRule") : null} hint={t("hw.suspectHelp")}>{(pp) => <input {...pp} className="nx-inp nx-mono" aria-label={t("hw.suspect")} type="number" min={1} max={59} value={form.suspect_threshold} onChange={(e) => setForm({ ...form, suspect_threshold: e.target.value })} />}</Field>
        <Field label={t("hw.failed")} unit={t("hw.rounds")} hint={t("hw.failedHelp")}>{(pp) => <input {...pp} className="nx-inp nx-mono" aria-label={t("hw.failed")} type="number" min={2} max={60} value={form.failure_threshold} onChange={(e) => setForm({ ...form, failure_threshold: e.target.value })} />}</Field>
        <div className="nx-fa"><button type="submit" className="nx-btn" disabled={busy || bad}>{t("sso.save")}</button></div>
      </form>
    </Card>
  );
}

const METHODS = ["ipmi", "redfish", "amt", "lease_only"];

function FencingDrawer({ node, name, current, onClose, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState({ method: "ipmi", address: "", port: "", user: "", secret: "", tls_unverified: false });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (node) setForm({ method: current?.method || "ipmi", address: current?.address || "", port: current?.port || "", user: current?.user || "", secret: "", tls_unverified: !!current?.tls_unverified });
  }, [node, current]);
  const bmc = form.method !== "lease_only";
  const missing = bmc && (!form.address.trim() || !form.user.trim() || (!form.secret && !current?.secret_set));
  async function save() {
    setBusy(true);
    try {
      await saveFencing(node, { method: form.method, address: bmc ? form.address.trim() : null, port: bmc && form.port ? Number(form.port) : null, user: bmc ? form.user.trim() : null, secret: form.secret || null, tls_unverified: form.tls_unverified });
      pushToast({ kind: "success", title: t("hw.fencingSaved"), message: node }); onDone(); onClose();
    } catch (er) { pushToast({ kind: "error", title: t("hw.fencingFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }
  return (
    <SideDrawer open={!!node} title={t("hw.fencingOf", { node: name || "" })} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || missing} onClick={save}>{t("sso.save")}</button>
    </>}>
      <Field label={t("hw.method")} hint={t(`hw.m.${form.method}Help`)}>{(p) => <select {...p} className="nx-inp" aria-label={t("hw.method")} value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>{METHODS.map((m) => <option key={m} value={m}>{t(`hw.m.${m}`)}</option>)}</select>}</Field>
      {bmc && <>
        <Field label={t("hw.address")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("hw.address")} placeholder="10.0.0.20" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />}</Field>
        <Field label={t("hw.port")} hint={t("hw.portHelp")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("hw.port")} type="number" min={1} max={65535} value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} />}</Field>
        <Field label={t("hw.user")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("hw.user")} autoComplete="off" value={form.user} onChange={(e) => setForm({ ...form, user: e.target.value })} />}</Field>
        <Field label={t("hw.password")} hint={current?.secret_set ? t("hw.passwordKept") : null}>{(p) => <input {...p} className="nx-inp" aria-label={t("hw.password")} type="password" autoComplete="new-password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />}</Field>
        {form.method !== "ipmi" && <label className="nx-check"><input type="checkbox" checked={form.tls_unverified} onChange={(e) => setForm({ ...form, tls_unverified: e.target.checked })} /> {t("hw.tlsInsecure")}</label>}
      </>}
    </SideDrawer>
  );
}

export function FencingCard({ nodes }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [list, setList] = useState(null);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(null);
  const [results, setResults] = useState({});
  const [loadError, setLoadError] = useState(null);
  // A failed load is not "no fencing configured": showing every node as unconfigured would
  // hide Test and Remove and invite overwriting a configuration that exists.
  const reload = useCallback(() => {
    setLoadError(null);
    return fetchFencing().then(setList).catch((e) => { setList(null); setLoadError(errorMessage(e)); });
  }, []);
  useEffect(() => { reload(); }, [reload]);
  const names = ["local", ...nodes.filter((n) => n.id !== "local").map((n) => n.id)];
  const byNode = Object.fromEntries((list || []).map((f) => [f.node, f]));
  const label = (id) => nodes.find((n) => n.id === id)?.name || id;
  async function test(node) {
    setBusy(node);
    try { const r = await testFencing(node); setResults({ ...results, [node]: r }); }
    catch (er) { setResults({ ...results, [node]: { ok: false, detail: errorMessage(er) } }); } finally { setBusy(null); }
  }
  async function leases(node) {
    setBusy(node);
    try { const r = await checkLeases(node); setResults({ ...results, [`${node}:l`]: { ok: r.active, detail: r.detail } }); }
    catch (er) { pushToast({ kind: "error", title: t("hw.leasesFailed"), message: errorMessage(er) }); } finally { setBusy(null); }
  }
  async function remove(node) {
    if (!(await confirmAction({ title: t("hw.removeTitle", { node: label(node) }), message: t("hw.removeMsg"), confirmLabel: t("hw.remove"), danger: true }))) return;
    try { await deleteFencing(node); reload(); } catch (er) { pushToast({ kind: "error", title: t("hw.fencingFailed"), message: errorMessage(er) }); }
  }
  return (
    <Card title={t("hw.fencing")} flush>
      <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("hw.fencingHelp")}</p>
      {loadError ? <div style={{ margin: "0 var(--space-4) var(--space-3)" }}><InlineError message={loadError} onRetry={reload} /></div> : list === null ? null : (
      <TableWrap>
        <table className="nx-table">
          <thead><tr><th scope="col">{t("ha.node")}</th><th scope="col">{t("hw.method")}</th><th scope="col">{t("hw.result")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
          <tbody>
            {names.map((node) => {
              const f = byNode[node];
              const r = results[node];
              const l = results[`${node}:l`];
              return (
                <tr key={node}>
                  <th scope="row" className="nx-mono">{label(node)}</th>
                  <td>{f ? <>{t(`hw.m.${f.method}`)}{f.address && <span className="nx-muted nx-mono"> · {f.address}</span>}</> : <span className="nx-muted">{t("hw.none")}</span>}</td>
                  <td className="nx-wrapcell">
                    {r && <div className={r.ok ? "" : "nx-f-h is-error"}>{r.ok && r.power ? t("hw.power", { state: r.power.toUpperCase() }) : r.detail}</div>}
                    {l && <div className={l.ok ? "nx-f-h" : "nx-f-h is-error"}>{t("hw.leasesResult", { detail: l.detail })}</div>}
                  </td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing(node)}>{t(f ? "hw.edit" : "hw.configure")}</button>
                    {f && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy === node} aria-label={t("hw.testAria", { node: label(node) })} onClick={() => test(node)}><Zap size={14} aria-hidden="true" />{t("hw.test")}</button>}
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy === node} aria-label={t("hw.leasesAria", { node: label(node) })} onClick={() => leases(node)}><ShieldCheck size={14} aria-hidden="true" />{t("hw.leases")}</button>
                    {f && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => remove(node)}>{t("hw.remove")}</button>}
                  </div></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
      )}
      <FencingDrawer node={editing} name={editing ? label(editing) : ""} current={editing ? byNode[editing] : null} onClose={() => setEditing(null)} onDone={reload} />
    </Card>
  );
}
