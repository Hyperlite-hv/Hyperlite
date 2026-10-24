import { useCallback, useEffect, useState } from "react";
import { Play, Plus, Repeat, Trash2 } from "lucide-react";
import { fetchStoreSyncs, createStoreSync, updateStoreSync, deleteStoreSync, runStoreSync, fetchBackupStores } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "./States";
import RetentionFields, { describeRetention, retentionForm, retentionPayload, retentionProblems } from "./RetentionFields";
import { Card, Chip, Empty, Field, Loading, SideDrawer, TableWrap } from "./ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$/;
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const EMPTY = { id: null, name: "", source: "", target: "", frequency: "daily", time: "04:00", active: true, ...retentionForm(null) };

// Off-site copies, as Proxmox Backup Server's sync jobs: a store's backups pulled into its copy store (on a pool at
// the other site), only the chunks it lacks, with a retention of its own: what the source deletes stays in the copy.
export default function BackupStoreSyncs() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [jobs, setJobs] = useState(null);
  const [stores, setStores] = useState([]);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setJobs(await fetchStoreSyncs()); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); fetchBackupStores().then(setStores).catch(() => setStores([])); }, [load]);

  const copies = stores.filter((s) => s.copy_of);
  const set = (k) => (e) => setForm((f) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    // A copy store has one source: choosing it fills the source in.
    if (k === "target") return { ...f, target: value, source: copies.find((s) => s.name === value)?.copy_of || "" };
    return { ...f, [k]: value };
  });
  const bad = form ? { name: !NAME_RE.test(form.name), target: !form.target, time: !TIME_RE.test(form.time), ...retentionProblems(form) } : {};
  const invalid = Object.values(bad).some(Boolean);

  async function save() {
    setBusy(true);
    const payload = { name: form.name.trim(), source: form.source, target: form.target, frequency: form.frequency, time: form.time, active: form.active, ...retentionPayload(form) };
    try {
      await (form.id ? updateStoreSync(form.id, payload) : createStoreSync(payload));
      pushToast({ kind: "success", title: t("bsy.saved"), message: payload.name });
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("bsy.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function run(j) {
    try { await runStoreSync(j.id); pushToast({ kind: "success", title: t("bsy.started"), message: j.name }); setTimeout(load, 1500); }
    catch (e) { pushToast({ kind: "error", title: t("bsy.runFailed"), message: errorMessage(e) }); }
  }
  async function remove(j) {
    if (!(await confirmAction({ title: t("bsy.deleteTitle", { name: j.name }), message: t("bsy.deleteMsg", { target: j.target }), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteStoreSync(j.id); await load(); } catch (e) { pushToast({ kind: "error", title: t("bsy.deleteFailed"), message: errorMessage(e) }); }
  }
  const edit = (j) => setForm({ ...EMPTY, ...j, ...retentionForm(j) });

  return (
    <>
      <Card title={t("bsy.title")} flush actions={<button type="button" className="nx-btn" onClick={() => setForm({ ...EMPTY })}><Plus size={15} aria-hidden="true" />{t("bsy.add")}</button>}>
        {error && !jobs ? <ErrorState message={error} onRetry={load} /> : !jobs ? <Loading style={{ padding: "var(--space-4)" }} /> : jobs.length === 0 ? <Empty icon={Repeat} title={t("bsy.none")} text={t("bsy.noneHelp")} /> : (
          <TableWrap label={t("bsy.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("bg.name")}</th><th scope="col">{t("bsy.route")}</th><th scope="col">{t("bg.when")}</th><th scope="col">{t("gfs.title")}</th><th scope="col">{t("bsy.last")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>{jobs.map((j) => (
                <tr key={j.id}>
                  <th scope="row">{j.name}{!j.active && <> <Chip>{t("mx.paused")}</Chip></>}{j.running && <> <Chip tone="info">{t("bg.running")}</Chip></>}</th>
                  <td className="nx-mono">{j.source} → {j.target}</td>
                  <td>{t(`vb.f.${j.frequency}`)} · <span className="nx-mono">{j.time}</span> UTC</td>
                  <td className="nx-wrapcell">{describeRetention(t, j)}</td>
                  <td className="nx-wrapcell">{j.last_run ? <><span className="nx-mono">{formatDateTime(j.last_run, lang)}</span><div className={`nx-f-h${(j.last_result || "").startsWith("failed") ? " is-error" : ""}`}>{j.last_result}</div></> : <span className="nx-muted">{t("bst.never")}</span>}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost" disabled={j.running} aria-label={t("bsy.runX", { name: j.name })} onClick={() => run(j)}><Play size={14} aria-hidden="true" />{t("bg.run")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("mx.editX", { name: j.name })} onClick={() => edit(j)}>{t("mx.edit")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("mx.deleteX", { name: j.name })} onClick={() => remove(j)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <SideDrawer open={!!form} title={form?.id ? t("bsy.editTitle") : t("bsy.add")} onClose={() => setForm(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setForm(null)}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={invalid || busy} onClick={save}>{t("mx.save")}</button>
      </>}>
        {form && <>
          <p className="nx-muted" style={{ margin: 0 }}>{t("bsy.help")}</p>
          <Field label={t("bg.name")} error={form.name && bad.name ? t("bst.nameRule") : null}>{(p) => <input {...p} className="nx-inp" value={form.name} onChange={set("name")} placeholder={t("bsy.namePh")} />}</Field>
          <Field label={t("bsy.target")} hint={copies.length ? (form.source ? t("bsy.fromX", { name: form.source }) : t("bsy.targetHint")) : t("bsy.noCopies")}>{(p) => <select {...p} className="nx-inp" value={form.target} onChange={set("target")}>
            <option value="">{t("sec.choose")}</option>{copies.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>}</Field>
          <div className="nx-fg">
            <Field label={t("vb.frequency")}>{(p) => <select {...p} className="nx-inp" value={form.frequency} onChange={set("frequency")}><option value="daily">{t("vb.f.daily")}</option><option value="weekly">{t("vb.f.weekly")}</option><option value="monthly">{t("vb.f.monthly")}</option></select>}</Field>
            <Field label={t("vb.time")} hint={t("vb.utc")} error={bad.time ? t("vb.timeRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="time" value={form.time} onChange={set("time")} />}</Field>
          </div>
          <p className="nx-f-h" style={{ margin: 0 }}>{t("bsy.retentionHelp")}</p>
          <RetentionFields value={form} onChange={(v) => setForm((f) => ({ ...f, ...v }))} />
          <label className="nx-check"><input type="checkbox" checked={form.active} onChange={set("active")} /> {t("bg.active")}</label>
        </>}
      </SideDrawer>
    </>
  );
}
