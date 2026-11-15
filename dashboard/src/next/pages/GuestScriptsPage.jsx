import { useCallback, useEffect, useMemo, useState } from "react";
import { Code, Copy, PencilLine, Play, Plus, Trash2 } from "lucide-react";
import { deleteGuestScript, fetchGuestScriptRuns, fetchGuestScripts, runGuestScript, saveGuestScript } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

const STATE_TONE = { done: "success", running: "accent", failed: "danger", skipped: undefined };

// Script library (docs/design/console-2.md, section 7): ready-made and the administrators' own scripts run in guests
// through their agent, in the background, followed every minute with each guest's exit code and output.
export default function GuestScriptsPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const [scripts, setScripts] = useState(null);
  const [runs, setRuns] = useState([]);
  const [running, setRunning] = useState(null);
  const [editing, setEditing] = useState(null);
  const [open, setOpen] = useState(null);
  const load = useCallback(() => Promise.all([fetchGuestScripts(), fetchGuestScriptRuns()]).then(([s, r]) => { setScripts(s || []); setRuns(r || []); }).catch((e) => { setScripts([]); pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }), [pushToast, t]);
  useEffect(() => { load(); const id = setInterval(() => fetchGuestScriptRuns().then((r) => setRuns(r || [])).catch(() => {}), 30000); return () => clearInterval(id); }, [load]);
  const localRunning = useMemo(() => vms.filter((v) => v.node === "local" && (v.state === "running" || v.state === "active")).map((v) => v.name).sort(), [vms]);
  const fail = (e) => pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) });
  async function remove(s) {
    if (!(await confirmAction({ title: t("gs.deleteTitle", { name: s.name }), message: t("gs.deleteMsg"), confirmLabel: t("gs.delete"), danger: true }))) return;
    try { await deleteGuestScript(s.id); load(); } catch (e) { fail(e); }
  }
  if (!scripts) return <><PageHeader title={t("tab.scripts")} desc={t("gs.desc")} /><Loading /></>;
  return (
    <>
      <PageHeader title={t("tab.scripts")} desc={t("gs.desc")} actions={caps.admin && <button type="button" className="nx-btn nx-btn--primary" onClick={() => setEditing({ name: "", description: "", shell: "sh", body: "", params: [] })}><Plus size={14} aria-hidden="true" /> {t("gs.new")}</button>} />
      <div className="hc-gs">
        {scripts.map((s) => (
          <article key={s.id} className="hc-dcard">
            <header className="hc-dcard-h">
              <h3>{s.name}</h3>
              <Chip>{s.shell === "powershell" ? "Windows" : "Linux"}</Chip>
              {s.builtin ? <Chip tone="info">{t("gs.builtin")}</Chip> : <Chip>{t("gs.custom")}</Chip>}
            </header>
            <p className="nx-muted" style={{ marginTop: 0 }}>{s.description}</p>
            <div className="nx-ra" style={{ justifyContent: "flex-start" }}>
              <button type="button" className="nx-btn nx-btn--sm nx-btn--primary" onClick={() => setRunning({ script: s, vms: [], params: Object.fromEntries(s.params.map((p) => [p.name, p.default])) })}><Play size={14} aria-hidden="true" /> {t("gs.run")}</button>
              <button type="button" className="nx-btn nx-btn--sm" onClick={() => setOpen(open === s.id ? null : s.id)}><Code size={14} aria-hidden="true" /> {open === s.id ? t("gs.hide") : t("gs.show")}</button>
              {caps.admin && s.builtin && <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({ name: t("gs.copyOf", { name: s.name }), description: s.description, shell: s.shell, body: s.body, params: s.params })}><Copy size={14} aria-hidden="true" /> {t("gs.copy")}</button>}
              {caps.admin && !s.builtin && <>
                <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("gs.edit", { name: s.name })} onClick={() => setEditing(s)}><PencilLine size={15} aria-hidden="true" /></button>
                <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("gs.deleteTitle", { name: s.name })} onClick={() => remove(s)}><Trash2 size={15} aria-hidden="true" /></button>
              </>}
            </div>
            {open === s.id && <pre className="hc-def nx-mono">{s.body}</pre>}
          </article>
        ))}
      </div>
      <Card title={t("gs.runs")} flush>
        {!runs.length ? <Empty icon={Play} title={t("gs.noRun")} text={t("gs.noRunText")} /> : (
          <TableWrap label={t("gs.runs")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("gs.when")}</th><th scope="col">{t("gs.script")}</th><th scope="col">VM</th><th scope="col">{t("ac.status")}</th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="nx-mono">{formatDateTime(r.created_at, lang)}<br /><small className="nx-muted">{r.created_by}</small></td>
                    <th scope="row">{r.script_name}</th>
                    <td>{r.vms.map((vm) => { const x = r.results[vm] || {}; return (
                      <details key={vm} className="hc-gs-res">
                        <summary><span className="nx-mono">{vm}</span> <Chip tone={STATE_TONE[x.state]}>{t(`gs.st.${x.state || "running"}`)}</Chip>{x.exit != null && <small className="nx-muted"> {t("gs.exit", { n: x.exit })}</small>}</summary>
                        {x.error && <p className="nx-err">{x.error}</p>}
                        {x.output && <pre className="hc-def nx-mono">{x.output}</pre>}
                      </details>
                    ); })}</td>
                    <td><Chip tone={r.status === "done" ? "success" : "accent"}>{t(`gs.run.${r.status}`)}</Chip></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {running && <RunDrawer t={t} state={running} vmNames={localRunning} onClose={() => setRunning(null)} onDone={(r) => { setRunning(null); setRuns((x) => [r, ...x]); pushToast({ kind: "success", title: t("gs.started"), message: r.script_name }); }} onFail={fail} />}
      {editing && <EditDrawer t={t} script={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); load(); }} onFail={fail} />}
    </>
  );
}
GuestScriptsPage.ownHeader = true;

function RunDrawer({ t, state, vmNames, onClose, onDone, onFail }) {
  const [f, setF] = useState(state);
  const [busy, setBusy] = useState(false);
  const toggle = (n) => setF((x) => ({ ...x, vms: x.vms.includes(n) ? x.vms.filter((y) => y !== n) : [...x.vms, n] }));
  async function go() {
    setBusy(true);
    try { onDone(await runGuestScript({ script_id: f.script.id, vms: f.vms, params: f.params })); } catch (e) { onFail(e); }
    setBusy(false);
  }
  return (
    <SideDrawer open title={t("gs.runTitle", { name: f.script.name })} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.vms.length || busy} onClick={go}>{t("gs.runOn", { n: f.vms.length })}</button>
    </>}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("gs.runNote")}</p>
      {f.script.params.map((p) => <Field key={p.name} label={p.label}>{(a) => <input {...a} className="nx-inp nx-mono" value={f.params[p.name] ?? ""} onChange={(e) => setF((x) => ({ ...x, params: { ...x.params, [p.name]: e.target.value } }))} />}</Field>)}
      <fieldset className="nx-fs">
        <legend>{t("gs.vms")}</legend>
        {!vmNames.length ? <p className="nx-muted">{t("gs.noVm")}</p> : vmNames.map((n) => <label key={n} className="nx-check"><input type="checkbox" checked={f.vms.includes(n)} onChange={() => toggle(n)} /> <span className="nx-mono">{n}</span></label>)}
      </fieldset>
    </SideDrawer>
  );
}

function EditDrawer({ t, script, onClose, onDone, onFail }) {
  const [f, setF] = useState({ ...script, params: script.params || [] });
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const setParam = (i, k, v) => setF((x) => ({ ...x, params: x.params.map((p, j) => (j === i ? { ...p, [k]: v } : p)) }));
  async function save() {
    try { await saveGuestScript({ name: f.name.trim(), description: f.description, shell: f.shell, body: f.body, params: f.params }, script.id && !script.builtin ? script.id : undefined); onDone(); } catch (e) { onFail(e); }
  }
  return (
    <SideDrawer open title={script.id && !script.builtin ? t("gs.editTitle", { name: script.name }) : t("gs.new")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim() || !f.body.trim()} onClick={save}>{t("gs.save")}</button>
    </>}>
      <Field label={t("gs.name")}>{(p) => <input {...p} className="nx-inp" maxLength={100} value={f.name} onChange={set("name")} />}</Field>
      <Field label={t("gs.description")}>{(p) => <input {...p} className="nx-inp" maxLength={500} value={f.description} onChange={set("description")} />}</Field>
      <Field label={t("gs.shell")}>{(p) => <select {...p} className="nx-inp" value={f.shell} onChange={set("shell")}><option value="sh">sh (Linux)</option><option value="powershell">PowerShell (Windows)</option></select>}</Field>
      <Field label={t("gs.body")} hint={t("gs.bodyHint")}>{(p) => <textarea {...p} className="nx-inp nx-mono" rows={14} spellCheck={false} value={f.body} onChange={set("body")} />}</Field>
      <fieldset className="nx-fs">
        <legend>{t("gs.params")}</legend>
        {f.params.map((p, i) => (
          <div key={i} className="nx-fg">
            <Field label={t("gs.pName")}>{(a) => <input {...a} className="nx-inp nx-mono" value={p.name} onChange={(e) => setParam(i, "name", e.target.value.toUpperCase())} placeholder="PORT" />}</Field>
            <Field label={t("gs.pLabel")}>{(a) => <input {...a} className="nx-inp" value={p.label || ""} onChange={(e) => setParam(i, "label", e.target.value)} />}</Field>
            <Field label={t("gs.pDefault")}>{(a) => <input {...a} className="nx-inp nx-mono" value={p.default || ""} onChange={(e) => setParam(i, "default", e.target.value)} />}</Field>
            <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setF((x) => ({ ...x, params: x.params.filter((_, j) => j !== i) }))}><Trash2 size={14} aria-hidden="true" /> {t("sec.remove")}</button>
          </div>
        ))}
        <button type="button" className="nx-btn nx-btn--sm" onClick={() => setF((x) => ({ ...x, params: [...x.params, { name: "", label: "", default: "" }] }))}><Plus size={14} aria-hidden="true" /> {t("gs.addParam")}</button>
      </fieldset>
    </SideDrawer>
  );
}
