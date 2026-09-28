import { useCallback, useEffect, useState } from "react";
import { FlaskConical, ShieldCheck, Zap } from "lucide-react";
import { fetchHaStatus, saveHaSettings, fetchFencing, saveFencing, deleteFencing, testFencing, checkLeases } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Card, Field, SideDrawer, TableWrap } from "./ui";

// Automatic HA in dry-run mode (docs/design/ha-automatic.md): the watcher's status, the witness and thresholds, and
// each node's fencing settings with a status-only test. Nothing here ever powers a node off or restarts a VM.
export function HaWatchBanner({ status }) {
  const t = useT();
  if (!status) return null;
  return (
    <div className="nx-bn" data-tone="info" role="status"><FlaskConical size={16} aria-hidden="true" />
      <span className="nx-bn-t"><b>{t("hw.dryRun")}</b> {t("hw.dryRunHelp")}{status.raison && <><br />{t("hw.autoOff", { reason: status.raison })}</>}</span>
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
  useEffect(() => { if (status && !form) setForm({ ...status.reglages }); }, [status, form]);
  if (!form) return null;
  const s = Number(form.seuil_suspect), p = Number(form.seuil_panne);
  const bad = !Number.isInteger(s) || !Number.isInteger(p) || s < 1 || s >= p || p > 60;
  async function save(e) {
    e.preventDefault();
    if (bad) return;
    setBusy(true);
    try { const r = await saveHaSettings({ temoin: form.temoin.trim(), seuil_suspect: s, seuil_panne: p }); setForm({ ...r }); pushToast({ kind: "success", title: t("hw.saved") }); onSaved(); }
    catch (er) { pushToast({ kind: "error", title: t("hw.saveFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("hw.settings")}>
      <form onSubmit={save} className="nx-fg nx-fg--3">
        <Field label={t("hw.witness")} hint={t("hw.witnessHelp")}>{(pp) => <input {...pp} className="nx-inp nx-mono" aria-label={t("hw.witness")} placeholder="nas.example.lan" value={form.temoin} onChange={(e) => setForm({ ...form, temoin: e.target.value })} />}</Field>
        <Field label={t("hw.suspect")} unit={t("hw.rounds")} error={bad ? t("hw.thresholdRule") : null} hint={t("hw.suspectHelp")}>{(pp) => <input {...pp} className="nx-inp nx-mono" aria-label={t("hw.suspect")} type="number" min={1} max={59} value={form.seuil_suspect} onChange={(e) => setForm({ ...form, seuil_suspect: e.target.value })} />}</Field>
        <Field label={t("hw.failed")} unit={t("hw.rounds")} hint={t("hw.failedHelp")}>{(pp) => <input {...pp} className="nx-inp nx-mono" aria-label={t("hw.failed")} type="number" min={2} max={60} value={form.seuil_panne} onChange={(e) => setForm({ ...form, seuil_panne: e.target.value })} />}</Field>
        <div className="nx-fa"><button type="submit" className="nx-btn" disabled={busy || bad}>{t("sso.save")}</button></div>
      </form>
    </Card>
  );
}

const METHODS = ["ipmi", "redfish", "amt", "lease_only"];

function FencingDrawer({ node, name, current, onClose, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState({ methode: "ipmi", adresse: "", port: "", utilisateur: "", secret: "", tls_non_verifie: false });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (node) setForm({ methode: current?.methode || "ipmi", adresse: current?.adresse || "", port: current?.port || "", utilisateur: current?.utilisateur || "", secret: "", tls_non_verifie: !!current?.tls_non_verifie });
  }, [node, current]);
  const bmc = form.methode !== "lease_only";
  const missing = bmc && (!form.adresse.trim() || !form.utilisateur.trim() || (!form.secret && !current?.secret_defini));
  async function save() {
    setBusy(true);
    try {
      await saveFencing(node, { methode: form.methode, adresse: bmc ? form.adresse.trim() : null, port: bmc && form.port ? Number(form.port) : null, utilisateur: bmc ? form.utilisateur.trim() : null, secret: form.secret || null, tls_non_verifie: form.tls_non_verifie });
      pushToast({ kind: "success", title: t("hw.fencingSaved"), message: node }); onDone(); onClose();
    } catch (er) { pushToast({ kind: "error", title: t("hw.fencingFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }
  return (
    <SideDrawer open={!!node} title={t("hw.fencingOf", { node: name || "" })} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || missing} onClick={save}>{t("sso.save")}</button>
    </>}>
      <Field label={t("hw.method")} hint={t(`hw.m.${form.methode}Help`)}>{(p) => <select {...p} className="nx-inp" aria-label={t("hw.method")} value={form.methode} onChange={(e) => setForm({ ...form, methode: e.target.value })}>{METHODS.map((m) => <option key={m} value={m}>{t(`hw.m.${m}`)}</option>)}</select>}</Field>
      {bmc && <>
        <Field label={t("hw.address")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("hw.address")} placeholder="10.0.0.20" value={form.adresse} onChange={(e) => setForm({ ...form, adresse: e.target.value })} />}</Field>
        <Field label={t("hw.port")} hint={t("hw.portHelp")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("hw.port")} type="number" min={1} max={65535} value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} />}</Field>
        <Field label={t("hw.user")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("hw.user")} autoComplete="off" value={form.utilisateur} onChange={(e) => setForm({ ...form, utilisateur: e.target.value })} />}</Field>
        <Field label={t("hw.password")} hint={current?.secret_defini ? t("hw.passwordKept") : null}>{(p) => <input {...p} className="nx-inp" aria-label={t("hw.password")} type="password" autoComplete="new-password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />}</Field>
        {form.methode !== "ipmi" && <label className="nx-check"><input type="checkbox" checked={form.tls_non_verifie} onChange={(e) => setForm({ ...form, tls_non_verifie: e.target.checked })} /> {t("hw.tlsInsecure")}</label>}
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
  const reload = useCallback(() => fetchFencing().then(setList).catch(() => setList([])), []);
  useEffect(() => { reload(); }, [reload]);
  const names = ["local", ...nodes.filter((n) => n.id !== "local").map((n) => n.id)];
  const byNode = Object.fromEntries((list || []).map((f) => [f.node, f]));
  const label = (id) => nodes.find((n) => n.id === id)?.nom || id;
  async function test(node) {
    setBusy(node);
    try { const r = await testFencing(node); setResults({ ...results, [node]: r }); }
    catch (er) { setResults({ ...results, [node]: { ok: false, detail: errorMessage(er) } }); } finally { setBusy(null); }
  }
  async function leases(node) {
    setBusy(node);
    try { const r = await checkLeases(node); setResults({ ...results, [`${node}:l`]: { ok: r.actif, detail: r.detail } }); }
    catch (er) { pushToast({ kind: "error", title: t("hw.leasesFailed"), message: errorMessage(er) }); } finally { setBusy(null); }
  }
  async function remove(node) {
    if (!(await confirmAction({ title: t("hw.removeTitle", { node: label(node) }), message: t("hw.removeMsg"), confirmLabel: t("hw.remove"), danger: true }))) return;
    try { await deleteFencing(node); reload(); } catch (er) { pushToast({ kind: "error", title: t("hw.fencingFailed"), message: errorMessage(er) }); }
  }
  return (
    <Card title={t("hw.fencing")} flush>
      <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("hw.fencingHelp")}</p>
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
                  <td>{f ? <>{t(`hw.m.${f.methode}`)}{f.adresse && <span className="nx-muted nx-mono"> · {f.adresse}</span>}</> : <span className="nx-muted">{t("hw.none")}</span>}</td>
                  <td className="nx-wrapcell">
                    {r && <div className={r.ok ? "" : "nx-f-h is-error"}>{r.ok && r.alimentation ? t("hw.power", { state: r.alimentation.toUpperCase() }) : r.detail}</div>}
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
      <FencingDrawer node={editing} name={editing ? label(editing) : ""} current={editing ? byNode[editing] : null} onClose={() => setEditing(null)} onDone={reload} />
    </Card>
  );
}
