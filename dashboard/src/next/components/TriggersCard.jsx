import { useCallback, useEffect, useState } from "react";
import { PencilLine, Plus, Trash2, Zap } from "lucide-react";
import { deleteTrigger, fetchTriggers, saveTrigger } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { Card, Chip, Empty, Field, SideDrawer, TableWrap } from "./ui";

const EMPTY = { name: "", event: "alert_fired", filter: "", job_id: "", pass_target: true, cooldown_min: 30, enabled: true };

// Triggers (docs/design/console-2.md, section 6): a job that runs by itself when an alert fires, a VM crashes, a backup
// or a guest update fails, or a node stops answering.
export default function TriggersCard({ jobs }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [data, setData] = useState(null);
  const [edit, setEdit] = useState(null);
  const load = useCallback(() => fetchTriggers().then(setData).catch(() => setData({ items: [], events: [] })), []);
  useEffect(() => { load(); }, [load]);
  const fail = (e) => pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) });
  const jobName = (id) => jobs.find((j) => j.id === id)?.name || `#${id}`;
  async function save(f) {
    try { await saveTrigger({ ...f, name: f.name.trim(), filter: f.filter.trim() || null, job_id: Number(f.job_id), cooldown_min: Number(f.cooldown_min) }, f.id); setEdit(null); load(); pushToast({ kind: "success", title: t("tg2.saved"), message: f.name }); } catch (e) { fail(e); }
  }
  async function remove(x) {
    if (!(await confirmAction({ title: t("tg2.deleteTitle", { name: x.name }), message: t("tg2.deleteMsg"), confirmLabel: t("tg2.delete"), danger: true }))) return;
    try { await deleteTrigger(x.id); load(); } catch (e) { fail(e); }
  }
  const items = data?.items || [];
  return (
    <Card title={t("tg2.title")} note={t("tg2.note")} flush actions={<button type="button" className="nx-btn nx-btn--sm" disabled={!jobs.length} title={!jobs.length ? t("tg2.needJob") : undefined} onClick={() => setEdit({ ...EMPTY, job_id: jobs[0]?.id ?? "" })}><Plus size={14} aria-hidden="true" /> {t("tg2.new")}</button>}>
      {!data ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("loading")}</p> : !items.length ? <Empty icon={Zap} title={t("tg2.none")} text={t("tg2.noneText")} /> : (
        <TableWrap label={t("tg2.title")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("tg2.name")}</th><th scope="col">{t("tg2.when")}</th><th scope="col">{t("tg2.job")}</th><th scope="col">{t("tg2.last")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {items.map((x) => (
                <tr key={x.id}>
                  <th scope="row">{x.name}{!x.enabled && <> <Chip>{t("pp.off")}</Chip></>}</th>
                  <td>{t(`tg2.ev.${x.event}`)}{x.filter && <> · <span className="nx-mono">{x.filter}</span></>}</td>
                  <td>{jobName(x.job_id)}{x.pass_target && x.event !== "node_unreachable" && <small className="nx-muted"> · {t("tg2.onSubject")}</small>}</td>
                  <td>{x.last_firing ? <>{formatDateTime(x.last_firing.at, lang)} <small className="nx-muted">{x.last_firing.subject} · {x.last_firing.outcome}</small></> : <span className="nx-muted">{t("tg2.never")}</span>}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("tg2.edit", { name: x.name })} onClick={() => setEdit({ ...EMPTY, ...x, filter: x.filter || "" })}><PencilLine size={15} aria-hidden="true" /></button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("tg2.deleteTitle", { name: x.name })} onClick={() => remove(x)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {edit && <TriggerDrawer t={t} trigger={edit} jobs={jobs} events={data?.events || []} onClose={() => setEdit(null)} onSave={save} />}
    </Card>
  );
}

function TriggerDrawer({ t, trigger, jobs, events, onClose, onSave }) {
  const [f, setF] = useState(trigger);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  return (
    <SideDrawer open title={trigger.id ? t("tg2.editTitle", { name: trigger.name }) : t("tg2.new")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim() || f.job_id === ""} onClick={() => onSave(f)}>{t("tg2.save")}</button>
    </>}>
      <Field label={t("tg2.name")}>{(p) => <input {...p} className="nx-inp" maxLength={100} value={f.name} onChange={set("name")} placeholder={t("tg2.namePh")} />}</Field>
      <Field label={t("tg2.when")}>{(p) => <select {...p} className="nx-inp" value={f.event} onChange={set("event")}>{events.map((e) => <option key={e} value={e}>{t(`tg2.ev.${e}`)}</option>)}</select>}</Field>
      <Field label={t("tg2.filter")} hint={t(`tg2.filterHint.${f.event}`)}>{(p) => <input {...p} className="nx-inp nx-mono" maxLength={200} value={f.filter} onChange={set("filter")} />}</Field>
      <Field label={t("tg2.job")}>{(p) => <select {...p} className="nx-inp" value={f.job_id} onChange={set("job_id")}>{jobs.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}</select>}</Field>
      {f.event !== "node_unreachable" && <label className="nx-check"><input type="checkbox" checked={f.pass_target} onChange={set("pass_target")} /> {t("tg2.passTarget")}</label>}
      <Field label={t("tg2.cooldown")} unit="min" hint={t("tg2.cooldownHint")}>{(p) => <input {...p} type="number" min={1} max={10080} className="nx-inp" value={f.cooldown_min} onChange={set("cooldown_min")} />}</Field>
      <label className="nx-check"><input type="checkbox" checked={f.enabled} onChange={set("enabled")} /> {t("pp.enabled")}</label>
    </SideDrawer>
  );
}
