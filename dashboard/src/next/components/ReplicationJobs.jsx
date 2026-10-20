import { useCallback, useEffect, useState } from "react";
import { Play, Plus, Repeat, Trash2 } from "lucide-react";
import {
  fetchReplicationJobs, createReplicationJob, updateReplicationJob, deleteReplicationJob, runReplicationJob, fetchReplicationStatus, fetchPools,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { allTags, useMetaStore } from "../lib/meta";
import { usePolling } from "../lib/polling";
import { isRemoteVm } from "../lib/vmId";
import { ErrorState } from "./States";
import { Card, Chip, Empty, Field, Loading, SideDrawer, TableWrap } from "./ui";

const EMPTY = { id: null, name: "", selection: "all", value: "", excluded: [], target_dir: "", interval_minutes: 15, active: true };

function age(t, seconds) {
  if (seconds == null) return t("rep.never");
  if (seconds < 90) return t("rep.ageSec", { n: seconds });
  if (seconds < 5400) return t("rep.ageMin", { n: Math.round(seconds / 60) });
  return t("rep.ageHour", { n: Math.round(seconds / 3600) });
}

// Replication to another site: an incremental copy of the chosen VMs every few minutes, to a storage of the other site,
// so that site can restart them with "Recovery of another site" losing at most one interval of changes.
export default function ReplicationJobs() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const byKey = useMetaStore((s) => s.byKey);
  const loadMeta = useMetaStore((s) => s.load);
  const [jobs, setJobs] = useState(null);
  const [states, setStates] = useState([]);
  const [pools, setPools] = useState([]);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [j, s] = await Promise.all([fetchReplicationJobs(), fetchReplicationStatus()]);
      setJobs(j); setStates(s); setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); loadMeta(); fetchPools().then(setPools).catch(() => setPools([])); }, [load, loadMeta]);
  usePolling(load, jobs?.some((j) => j.running) ? 5000 : 30000);

  const localVms = vms.filter((v) => !isRemoteVm(v)).map((v) => v.name).sort();
  const tags = allTags(byKey, "vm");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value, ...(k === "selection" ? { value: "" } : {}) }));
  const interval = Number(form?.interval_minutes);
  const bad = form ? {
    name: !form.name.trim(), target: !form.target_dir.trim().startsWith("/"),
    intervalle: !Number.isInteger(interval) || interval < 5 || interval > 1440,
    value: form.selection !== "all" && !form.value,
  } : {};
  const invalid = Object.values(bad).some(Boolean);
  const selectionLabel = (j) => (j.selection === "all" ? t("bg.sel.all") : j.selection === "tag" ? t("bg.tagX", { tag: j.value })
    : t("bg.poolX", { pool: pools.find((p) => String(p.id) === String(j.value))?.name || `#${j.value}` }));

  async function save() {
    setBusy(true);
    const payload = { name: form.name.trim(), selection: form.selection, value: form.selection === "all" ? null : form.value, excluded: form.excluded,
      target_dir: form.target_dir.trim(), interval_minutes: interval, active: form.active };
    try {
      await (form.id ? updateReplicationJob(form.id, payload) : createReplicationJob(payload));
      pushToast({ kind: "success", title: t("rep.saved"), message: payload.name });
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("rep.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function run(j) {
    try { await runReplicationJob(j.id); pushToast({ kind: "success", title: t("rep.started"), message: j.name }); await load(); }
    catch (e) { pushToast({ kind: "error", title: t("rep.runFailed"), message: errorMessage(e) }); }
  }
  async function remove(j) {
    if (!(await confirmAction({ title: t("rep.deleteTitle", { name: j.name }), message: t("rep.deleteMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteReplicationJob(j.id); await load(); } catch (e) { pushToast({ kind: "error", title: t("rep.deleteFailed"), message: errorMessage(e) }); }
  }
  const edit = (j) => setForm({ ...EMPTY, ...j, value: j.value || "" });
  const late = states.filter((s) => s.late);

  return (
    <>
      <Card title={t("rep.title")} flush actions={<button type="button" className="nx-btn" onClick={() => setForm({ ...EMPTY })}><Plus size={15} aria-hidden="true" />{t("rep.add")}</button>}>
        {error && !jobs ? <ErrorState message={error} onRetry={load} /> : !jobs ? <Loading style={{ padding: "var(--space-4)" }} /> : jobs.length === 0 ? <Empty icon={Repeat} title={t("rep.none")} text={t("rep.noneHelp")} /> : (
          <TableWrap label={t("rep.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("bg.name")}</th><th scope="col">{t("bg.selection")}</th><th scope="col">{t("rep.target")}</th><th scope="col">{t("rep.every")}</th><th scope="col">{t("vb.next")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>{jobs.map((j) => (
                <tr key={j.id}>
                  <th scope="row">{j.name}{!j.active && <> <Chip>{t("mx.paused")}</Chip></>}{j.running && <> <Chip tone="info">{t("bg.running")}</Chip></>}</th>
                  <td className="nx-wrapcell"><span title={j.vms.join(", ")}>{selectionLabel(j)} · {t("bg.nVms", { n: j.vms.length })}</span></td>
                  <td className="nx-mono nx-wrapcell">{j.target_dir}</td>
                  <td>{t("rep.everyN", { n: j.interval_minutes })}</td>
                  <td className="nx-mono">{j.active ? formatDateTime(j.next_run, lang) : "—"}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost" disabled={j.running || j.vms.length === 0} aria-label={t("rep.runX", { name: j.name })} onClick={() => run(j)}><Play size={14} aria-hidden="true" />{t("rep.run")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("mx.editX", { name: j.name })} onClick={() => edit(j)}>{t("mx.edit")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("mx.deleteX", { name: j.name })} onClick={() => remove(j)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
        {states.length > 0 && (
          <TableWrap label={t("rep.vmsTitle")}>
            {late.length > 0 && <div className="nx-bn" data-tone="warning" role="status"><span className="nx-bn-t">{t("rep.late", { n: late.length, list: late.map((s) => s.vm_name).join(", ") })}</span></div>}
            <table className="nx-table">
              <caption className="nx-sr">{t("rep.vmsTitle")}</caption>
              <thead><tr><th scope="col">VM</th><th scope="col">{t("rep.lastCopy")}</th><th scope="col">{t("rep.chain")}</th><th scope="col">{t("ns.col.state")}</th></tr></thead>
              <tbody>{states.map((s) => (
                <tr key={s.vm_name}>
                  <th scope="row">{s.vm_name}</th>
                  <td>{age(t, s.age_s)}{s.late && <> <Chip tone="warning">{t("rep.lateChip")}</Chip></>}</td>
                  <td>{t("rep.points", { n: s.points })}</td>
                  <td className="nx-wrapcell">{s.status === "failed" ? <span className="nx-tone-danger">{s.error}</span> : s.status === "ok" ? t("rep.ok") : t("rep.never")}</td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <SideDrawer open={!!form} title={form?.id ? t("rep.editTitle") : t("rep.add")} onClose={() => setForm(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setForm(null)}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={invalid || busy} onClick={save}>{t("mx.save")}</button>
      </>}>
        {form && <>
          <p className="nx-muted" style={{ margin: 0 }}>{t("rep.help")}</p>
          <Field label={t("bg.name")}>{(p) => <input {...p} className="nx-inp" value={form.name} onChange={set("name")} placeholder={t("rep.namePh")} />}</Field>
          <div className="nx-fg">
            <Field label={t("bg.selection")}>{(p) => <select {...p} className="nx-inp" value={form.selection} onChange={set("selection")}>
              <option value="all">{t("bg.sel.all")}</option><option value="tag">{t("bg.sel.tag")}</option><option value="pool">{t("bg.sel.pool")}</option>
            </select>}</Field>
            {form.selection === "tag" && <Field label={t("bg.tag")} hint={tags.length ? null : t("bg.noTags")}>{(p) => <select {...p} className="nx-inp" value={form.value} onChange={set("value")}><option value="">{t("sec.choose")}</option>{tags.map((tg) => <option key={tg} value={tg}>{tg}</option>)}</select>}</Field>}
            {form.selection === "pool" && <Field label={t("bg.pool")} hint={pools.length ? null : t("bg.noPools")}>{(p) => <select {...p} className="nx-inp" value={form.value} onChange={set("value")}><option value="">{t("sec.choose")}</option>{pools.map((pl) => <option key={pl.id} value={String(pl.id)}>{pl.name}</option>)}</select>}</Field>}
          </div>
          {localVms.length > 0 && (
            <fieldset className="nx-fs">
              <legend>{t("bg.exclude")}</legend>
              <div className="nx-checks">{localVms.map((v) => (
                <label key={v} className="nx-check"><input type="checkbox" checked={form.excluded.includes(v)} onChange={(e) => setForm((f) => ({ ...f, excluded: e.target.checked ? [...f.excluded, v] : f.excluded.filter((x) => x !== v) }))} /> {v}</label>
              ))}</div>
            </fieldset>
          )}
          <Field label={t("rep.target")} hint={t("rep.targetHint")} error={form.target_dir && bad.target ? t("rep.targetRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.target_dir} placeholder="/var/lib/libvirt/hyperlite-pools/site-b" onChange={set("target_dir")} />}</Field>
          <Field label={t("rep.interval")} hint={t("rep.intervalHint")} unit="min" error={bad.intervalle ? t("rep.intervalRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={5} max={1440} value={form.interval_minutes} onChange={set("interval_minutes")} />}</Field>
          <label className="nx-check"><input type="checkbox" checked={form.active} onChange={set("active")} /> {t("bg.active")}</label>
        </>}
      </SideDrawer>
    </>
  );
}
