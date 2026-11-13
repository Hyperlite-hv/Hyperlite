import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTemplates, deployTemplate, deleteTemplate, renameTemplate } from "../../api/client";
import { askNewName } from "../lib/rename";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { promptText } from "../../store/usePromptStore";
import { useT, useLangStore } from "../i18n";
import { ActionsContextMenu, useContextTarget } from "../components/ContextMenu";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import { ErrorState } from "../components/States";
import { Empty, Field, Loading, SideDrawer, TableWrap } from "../components/ui";
import { Check, CircleAlert, Copy, LayoutTemplate, LoaderCircle, Trash2 } from "lucide-react";
import { bulkNames, bulkProblems } from "../lib/bulkNames";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;

const MAX_COPIES = 20;

// Several copies of a template at once: named prefix-01, prefix-02..., deployed one after the other (each deployment
// copies or links a disk), each line saying how it went. Closing the panel stops before the next copy.
function BulkDeployDrawer({ tpl, onClose, onDone }) {
  const t = useT();
  const vms = useInfraStore((s) => s.vms);
  const [prefix, setPrefix] = useState(tpl.name);
  const [count, setCount] = useState(3);
  const [start, setStart] = useState(1);
  const [linked, setLinked] = useState(Boolean(tpl.linkable));
  const [state, setState] = useState({}); // name -> "running" | "done" | error message
  const [running, setRunning] = useState(false);
  const stop = useRef(false);
  const names = bulkNames(prefix.trim(), start, count);
  const problems = bulkProblems(names, new Set(vms.map((v) => v.name)));
  const blocked = Object.keys(problems).length > 0;
  const started = Object.keys(state).length > 0;
  async function run() {
    setRunning(true); stop.current = false;
    let ok = 0;
    for (const name of names) {
      if (stop.current) break;
      setState((s) => ({ ...s, [name]: "running" }));
      try { await deployTemplate(tpl.name, name, null, linked); ok += 1; setState((s) => ({ ...s, [name]: "done" })); }
      catch (e) { setState((s) => ({ ...s, [name]: errorMessage(e) })); }
    }
    setRunning(false);
    onDone(ok);
  }
  const close = () => { stop.current = true; onClose(); };
  return (
    <SideDrawer open title={t("tp.bulkTitle", { name: tpl.name })} onClose={close} busy={false} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={close}>{started && !running ? t("action.close") : t("action.cancel")}</button>
      {!started && <button type="button" className="nx-btn nx-btn--primary" disabled={blocked || running} onClick={run}>{t("tp.bulkGo", { n: count })}</button>}
    </>}>
      <p className="nx-hint">{t("tp.bulkHelp")}</p>
      <Field label={t("tp.bulkPrefix")}>{(p) => <input {...p} className="nx-inp nx-mono" value={prefix} disabled={started} onChange={(e) => setPrefix(e.target.value)} />}</Field>
      <div className="hc-al-row">
        <Field label={t("tp.bulkCount")}>{(p) => <input {...p} className="nx-inp" type="number" min={1} max={MAX_COPIES} value={count} disabled={started} onChange={(e) => setCount(Math.max(1, Math.min(MAX_COPIES, Number(e.target.value) || 1)))} />}</Field>
        <Field label={t("tp.bulkStart")}>{(p) => <input {...p} className="nx-inp" type="number" min={0} max={9999} value={start} disabled={started} onChange={(e) => setStart(Math.max(0, Math.min(9999, Number(e.target.value) || 0)))} />}</Field>
      </div>
      {tpl.linkable && <label className="nx-check"><input type="checkbox" checked={linked} disabled={started} onChange={(e) => setLinked(e.target.checked)} />{t("tp.linked")}</label>}
      <h3 className="hc-al-h">{t("tp.bulkNames")}</h3>
      <ul className="hc-bulk">
        {names.map((n) => {
          const st = state[n];
          const bad = problems[n];
          return (
            <li key={n} data-state={bad ? "bad" : st === "done" ? "done" : st === "running" ? "running" : st ? "failed" : undefined}>
              {st === "done" ? <Check size={14} aria-hidden="true" /> : st === "running" ? <LoaderCircle size={14} className="nx-spin" aria-hidden="true" /> : bad || (st && st !== "running") ? <CircleAlert size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
              <span className="nx-mono">{n}</span>
              <small>{bad === "taken" ? t("tp.nameTaken") : bad === "rule" ? t("ct.nameRule") : st === "done" ? t("tp.bulkDone") : st === "running" ? t("tp.bulkRunning") : st || ""}</small>
            </li>
          );
        })}
      </ul>
    </SideDrawer>
  );
}

// Templates: VMs converted to read-only sources. Converting a stopped VM is done from its own page
// (Actions ▸ Convert to template); here a copy is deployed under a validated name, or the template deleted.
// A linked clone shares the template's disk and keeps only its own changes: the server refuses to delete or rename a
// template while one exists, so the list says which VMs read through each template.
export default function TemplatesPanel() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const vms = useInfraStore((s) => s.vms);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [bulk, setBulk] = useState(null);

  const reload = useCallback(async () => {
    try { const r = await fetchTemplates(); setItems(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { reload(); }, [reload]);

  const ctx = useContextTarget(); // right click on a template: its actions
  async function deploy(tpl) {
    const taken = new Set(vms.map((v) => v.name));
    const answer = await promptText({
      title: t("tp.deployTitle", { name: tpl.name }), label: t("tp.newName"), defaultValue: `${tpl.name}-01`, confirmLabel: t("tp.deploy"),
      validate: (v) => (!NAME_RE.test(v) ? t("ct.nameRule") : taken.has(v) ? t("tp.nameTaken") : ""),
      option: tpl.linkable ? { label: t("tp.linked"), hint: t("tp.linkedHint") } : undefined,
    });
    const name = typeof answer === "string" ? answer.trim() : answer?.value?.trim();
    if (!name) return;
    const linked = Boolean(answer?.option);
    setBusy(tpl.name);
    try {
      await deployTemplate(tpl.name, name, null, linked);
      pushToast({ kind: "success", title: linked ? t("tp.deployedLinked") : t("tp.deployed"), message: name });
      await Promise.all([refreshAll(), reload()]);
    }
    catch (e) { pushToast({ kind: "error", title: t("tp.deployFailed"), message: errorMessage(e) }); }
    finally { setBusy(null); }
  }
  async function rename(tpl) {
    const name = await askNewName(t, { title: t("rn.tplTitle", { name: tpl.name }), message: t("rn.tplMsg"), current: tpl.name });
    if (!name) return;
    try { await renameTemplate(tpl.name, name); pushToast({ kind: "success", title: t("rn.done"), message: `${tpl.name} → ${name}` }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("rn.failed"), message: errorMessage(e) }); }
  }
  async function remove(tpl) {
    if (!(await confirmAction({ title: t("tp.deleteTitle", { name: tpl.name }), message: t("tp.deleteMsg"), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    setBusy(tpl.name);
    try { await deleteTemplate(tpl.name); pushToast({ kind: "success", title: t("tp.deleted"), message: tpl.name }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("tp.deleteFailed"), message: errorMessage(e) }); }
    finally { setBusy(null); }
  }

  if (error && items == null) return <ErrorState message={error} onRetry={reload} />;
  const list = items || [];
  return (
    <div className="nx-card2 nx-card2--flush">
      {items == null ? <Loading style={{ padding: "var(--space-4)" }} /> : list.length === 0 ? <Empty icon={LayoutTemplate} title={t("tp.none")} text={t("tp.noneHelp")} /> : (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("ct.name")}</th><th scope="col">{t("tp.source")}</th><th scope="col" className="nx-num">vCPU</th><th scope="col" className="nx-num">{t("ct.memory")}</th><th scope="col">{t("tp.created")}</th><th scope="col">{t("tp.linkedClones")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {list.map((tpl) => (
                <tr key={tpl.name} className={ctx.is("tpl", tpl.name) ? "is-ctx" : undefined} onContextMenu={ctx.open("tpl", tpl)}>
                  <th scope="row" className="nx-mono">{tpl.name}</th>
                  <td className="nx-mono">{tpl.vm_source}</td>
                  <td className="nx-num nx-mono">{tpl.vcpu}</td>
                  <td className="nx-num nx-mono">{formatSizeMb(tpl.memory_mb, lang)}</td>
                  <td>{tpl.created_by} · <span className="nx-mono">{tpl.created_at}</span></td>
                  <td className="nx-mono">{tpl.linked_clones == null ? <span title={t("tp.linkedUnknown")}>—</span> : tpl.linked_clones.length === 0 ? "0" : tpl.linked_clones.join(", ")}</td>
                  <td><div className="nx-ra">
                    {caps.admin && <button type="button" className="nx-btn nx-btn--sm" disabled={busy === tpl.name} aria-label={t("a11y.deploy_template_x", { v: tpl.name })} onClick={() => deploy(tpl)}>{t("tp.deploy")}</button>}
                    {caps.admin && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy === tpl.name} aria-label={t("tp.bulkOf", { name: tpl.name })} onClick={() => setBulk(tpl)}>{t("tp.bulk")}</button>}
                    {caps.admin && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" disabled={busy === tpl.name} aria-label={t("a11y.delete_template_x", { v: tpl.name })} title={t("menu.delete").replace("…", "")} onClick={() => remove(tpl)}><Trash2 size={15} aria-hidden="true" /></button>}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
          <ActionsContextMenu ctx={ctx} label={(tpl) => t("ctx.menuOf", { name: tpl.name })} entries={(tpl) => [
            { key: "deploy", icon: "deploy", label: t("tp.deploy"), run: () => deploy(tpl), disabled: !caps.admin || busy === tpl.name, reason: t("menu.reason.admin") },
            { key: "bulk", icon: "deploy", label: t("tp.bulk"), run: () => setBulk(tpl), disabled: !caps.admin || busy === tpl.name, reason: t("menu.reason.admin") },
            "-",
            { key: "rename", icon: "rename", label: t("vx.renameMenu"), run: () => rename(tpl), disabled: !caps.admin || busy === tpl.name, reason: t("menu.reason.admin") },
            { key: "delete", icon: "delete", label: t("vx.delete"), danger: true, run: () => remove(tpl), disabled: !caps.admin || busy === tpl.name, reason: t("menu.reason.admin") },
          ]} />
        </TableWrap>
      )}
      {bulk && <BulkDeployDrawer tpl={bulk} onClose={() => setBulk(null)} onDone={(n) => { if (n) { pushToast({ kind: "success", title: t("tp.bulkFinished", { n }), message: bulk.name }); refreshAll(); reload(); } }} />}
    </div>
  );
}
