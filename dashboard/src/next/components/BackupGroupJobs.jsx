import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Play, Plus, Trash2 } from "lucide-react";
import { fetchBackupGroups, createBackupGroup, updateBackupGroup, deleteBackupGroup, runBackupGroup, fetchPools } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { allTags, useMetaStore } from "../lib/meta";
import { isRemoteVm } from "../lib/vmId";
import { ErrorState } from "./States";
import RetentionFields, { describeRetention, retentionForm, retentionPayload, retentionProblems } from "./RetentionFields";
import { Card, Chip, Empty, Field, Loading, SideDrawer, TableWrap } from "./ui";

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const EMPTY = { id: null, nom: "", selection: "toutes", valeur: "", exclues: [], frequence: "quotidien", heure: "02:00", cible_dir: "", actif: true, ...retentionForm(null) };

// Grouped backup jobs: one schedule for all of this node's VMs, those of a tag or of a pool, resolved at each run.
export default function BackupGroupJobs({ onChange }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const byKey = useMetaStore((s) => s.byKey);
  const loadMeta = useMetaStore((s) => s.load);
  const [jobs, setJobs] = useState(null);
  const [pools, setPools] = useState([]);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const j = await fetchBackupGroups(); setJobs(j); setError(null); onChange?.(j); } catch (e) { setError(errorMessage(e)); }
  }, [onChange]);
  useEffect(() => { load(); loadMeta(); fetchPools().then(setPools).catch(() => setPools([])); }, [load, loadMeta]);

  const localVms = vms.filter((v) => !isRemoteVm(v)).map((v) => v.nom).sort();
  const tags = allTags(byKey, "vm");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value, ...(k === "selection" ? { valeur: "" } : {}) }));
  const bad = form ? { nom: !form.nom.trim(), heure: !TIME_RE.test(form.heure), valeur: form.selection !== "toutes" && !form.valeur, ...retentionProblems(form) } : {};
  const invalid = Object.values(bad).some(Boolean);
  const selectionLabel = (j) => (j.selection === "toutes" ? t("bg.sel.toutes") : j.selection === "etiquette" ? t("bg.tagX", { tag: j.valeur })
    : t("bg.poolX", { pool: pools.find((p) => String(p.id) === String(j.valeur))?.name || `#${j.valeur}` }));

  async function save() {
    setBusy(true);
    const payload = { nom: form.nom.trim(), selection: form.selection, valeur: form.selection === "toutes" ? null : form.valeur, exclues: form.exclues,
      frequence: form.frequence, heure: form.heure, cible_dir: form.cible_dir.trim() || null, actif: form.actif, ...retentionPayload(form) };
    try {
      await (form.id ? updateBackupGroup(form.id, payload) : createBackupGroup(payload));
      pushToast({ kind: "success", title: t("bg.saved"), message: payload.nom });
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("bg.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function run(j) {
    if (!(await confirmAction({ title: t("bg.runTitle", { name: j.nom }), message: t("bg.runMsg", { n: j.vms.length, list: j.vms.join(", ") }), confirmLabel: t("bg.run") }))) return;
    try { await runBackupGroup(j.id); pushToast({ kind: "success", title: t("bg.started"), message: j.nom }); await load(); }
    catch (e) { pushToast({ kind: "error", title: t("bg.runFailed"), message: errorMessage(e) }); }
  }
  async function remove(j) {
    if (!(await confirmAction({ title: t("bg.deleteTitle", { name: j.nom }), message: t("bg.deleteMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteBackupGroup(j.id); await load(); } catch (e) { pushToast({ kind: "error", title: t("bg.deleteFailed"), message: errorMessage(e) }); }
  }
  const edit = (j) => setForm({ ...EMPTY, ...j, valeur: j.valeur || "", cible_dir: j.cible_dir || "", ...retentionForm(j) });

  return (
    <>
      <Card title={t("bg.title")} flush actions={<button type="button" className="nx-btn" onClick={() => setForm({ ...EMPTY })}><Plus size={15} aria-hidden="true" />{t("bg.add")}</button>}>
        {error && !jobs ? <ErrorState message={error} onRetry={load} /> : !jobs ? <Loading style={{ padding: "var(--space-4)" }} /> : jobs.length === 0 ? <Empty icon={CalendarClock} title={t("bg.none")} text={t("bg.noneHelp")} /> : (
          <TableWrap label={t("bg.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("bg.name")}</th><th scope="col">{t("bg.selection")}</th><th scope="col">{t("bg.when")}</th><th scope="col">{t("gfs.title")}</th><th scope="col">{t("vb.next")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>{jobs.map((j) => (
                <tr key={j.id}>
                  <th scope="row">{j.nom}{!j.actif && <> <Chip>{t("mx.paused")}</Chip></>}{j.en_cours && <> <Chip tone="info">{t("bg.running")}</Chip></>}</th>
                  <td className="nx-wrapcell"><span title={j.vms.join(", ")}>{selectionLabel(j)} · {t("bg.nVms", { n: j.vms.length })}</span>{j.exclues.length > 0 && <div className="nx-f-h">{t("bg.except", { list: j.exclues.join(", ") })}</div>}</td>
                  <td>{t(`vb.f.${j.frequence}`)} · <span className="nx-mono">{j.heure}</span> UTC</td>
                  <td className="nx-wrapcell">{describeRetention(t, j)}</td>
                  <td className="nx-mono">{j.actif ? formatDateTime(j.prochaine_execution, lang) : "—"}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost" disabled={j.en_cours || j.vms.length === 0} aria-label={t("bg.runX", { name: j.nom })} onClick={() => run(j)}><Play size={14} aria-hidden="true" />{t("bg.run")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("mx.editX", { name: j.nom })} onClick={() => edit(j)}>{t("mx.edit")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("mx.deleteX", { name: j.nom })} onClick={() => remove(j)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <SideDrawer open={!!form} title={form?.id ? t("bg.editTitle") : t("bg.add")} onClose={() => setForm(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setForm(null)}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={invalid || busy} onClick={save}>{t("mx.save")}</button>
      </>}>
        {form && <>
          <p className="nx-muted" style={{ margin: 0 }}>{t("bg.help")}</p>
          <Field label={t("bg.name")}>{(p) => <input {...p} className="nx-inp" value={form.nom} onChange={set("nom")} placeholder={t("bg.namePh")} />}</Field>
          <div className="nx-fg">
            <Field label={t("bg.selection")}>{(p) => <select {...p} className="nx-inp" value={form.selection} onChange={set("selection")}>
              <option value="toutes">{t("bg.sel.toutes")}</option><option value="etiquette">{t("bg.sel.etiquette")}</option><option value="pool">{t("bg.sel.pool")}</option>
            </select>}</Field>
            {form.selection === "etiquette" && <Field label={t("bg.tag")} hint={tags.length ? null : t("bg.noTags")}>{(p) => <select {...p} className="nx-inp" value={form.valeur} onChange={set("valeur")}><option value="">{t("sec.choose")}</option>{tags.map((tg) => <option key={tg} value={tg}>{tg}</option>)}</select>}</Field>}
            {form.selection === "pool" && <Field label={t("bg.pool")} hint={pools.length ? null : t("bg.noPools")}>{(p) => <select {...p} className="nx-inp" value={form.valeur} onChange={set("valeur")}><option value="">{t("sec.choose")}</option>{pools.map((pl) => <option key={pl.id} value={String(pl.id)}>{pl.name}</option>)}</select>}</Field>}
          </div>
          {localVms.length > 0 && (
            <fieldset className="nx-fs">
              <legend>{t("bg.exclude")}</legend>
              <div className="nx-checks">{localVms.map((v) => (
                <label key={v} className="nx-check"><input type="checkbox" checked={form.exclues.includes(v)} onChange={(e) => setForm((f) => ({ ...f, exclues: e.target.checked ? [...f.exclues, v] : f.exclues.filter((x) => x !== v) }))} /> {v}</label>
              ))}</div>
            </fieldset>
          )}
          <div className="nx-fg">
            <Field label={t("vb.frequency")}>{(p) => <select {...p} className="nx-inp" value={form.frequence} onChange={set("frequence")}><option value="quotidien">{t("vb.f.quotidien")}</option><option value="hebdomadaire">{t("vb.f.hebdomadaire")}</option><option value="mensuel">{t("vb.f.mensuel")}</option></select>}</Field>
            <Field label={t("vb.time")} hint={t("vb.utc")} error={bad.heure ? t("vb.timeRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="time" value={form.heure} onChange={set("heure")} />}</Field>
          </div>
          <Field label={t("bg.target")} hint={t("bg.targetHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.cible_dir} placeholder="/root/hyperlite/data/backups" onChange={set("cible_dir")} />}</Field>
          <RetentionFields value={form} onChange={(v) => setForm((f) => ({ ...f, ...v }))} />
          <label className="nx-check"><input type="checkbox" checked={form.actif} onChange={set("actif")} /> {t("bg.active")}</label>
        </>}
      </SideDrawer>
    </>
  );
}
