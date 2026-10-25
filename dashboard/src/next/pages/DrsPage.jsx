import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Pencil, Play, Plus, RefreshCw, Scale, Trash2, X } from "lucide-react";
import { applyDrsMove, deleteDrsRule, fetchDrs, saveDrsRule, saveDrsSettings, setDrsBehaviour } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import { Card, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

// Load balancing of the cluster (docs/design/drs.md, #334): the members' loads, the moves it recommends (applied by
// the leader in automatic mode, by an administrator here in manual mode), its settings, its rules and each VM's
// exception. Every change is checked again by the server.

const MODES = ["automatic", "manual", "off"];
const SPREAD = { 1: null, 2: 40, 3: 25, 4: 15, 5: 10 }; // the threshold's levels, as app/core/drs.py
const KINDS = ["together", "apart", "on_nodes", "not_on_nodes"];
const withNodes = (kind) => kind === "on_nodes" || kind === "not_on_nodes";
const scoreTone = (s) => (s >= 90 ? "danger" : s >= 75 ? "warning" : "info");
const pct = (v) => (v == null ? "—" : `${Math.round(v)} %`);

export default function DrsPage() {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const vms = useInfraStore((s) => s.vms);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null); // a rule, or {} for a new one

  const reload = useCallback(async () => {
    try { setData(await fetchDrs()); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { reload(); const id = setInterval(reload, 15000); return () => clearInterval(id); }, [reload]);

  async function apply(move) {
    if (!(await confirmAction({ title: t("drs.applyTitle", { vm: move.vm, node: move.target }), message: t("drs.applyMsg"), confirmLabel: t("drs.apply") }))) return;
    try { await applyDrsMove(move); pushToast({ kind: "success", title: t("drs.applied"), message: `${move.vm} → ${move.target}` }); reload(); }
    catch (e) { pushToast({ kind: "error", title: t("action.failed", { action: t("drs.apply") }), message: errorMessage(e) }); }
  }
  async function removeRule(name) {
    if (!(await confirmAction({ title: t("drs.ruleDeleteTitle", { name }), message: t("drs.ruleDeleteMsg"), confirmLabel: t("drs.delete"), danger: true }))) return;
    try { await deleteDrsRule(name); pushToast({ kind: "success", title: t("drs.ruleDeleted"), message: name }); reload(); }
    catch (e) { pushToast({ kind: "error", title: t("action.failed", { action: t("drs.delete") }), message: errorMessage(e) }); }
  }

  const header = <PageHeader title={t("tab.drs")} desc={t("drs.desc")} actions={<button type="button" className="nx-btn" onClick={reload}><RefreshCw size={15} aria-hidden="true" />{t("action.refresh")}</button>} />;
  if (error && !data) return <>{header}<ErrorState message={error} onRetry={reload} /></>;
  if (!data) return <>{header}<Loading style={{ padding: "var(--space-4)" }} /></>;
  const { settings, state, rules, behaviours } = data;
  const vmNames = [...new Set((vms || []).map((v) => v.name).filter(Boolean))].sort();
  const members = Object.keys(state.nodes || {}).sort();

  return (
    <>
      {header}
      <StateBanner settings={settings} state={state} admin={caps.admin} onSaved={reload} />
      <MembersCard nodes={state.nodes || {}} spread={state.spread} level={settings.threshold} />
      <MovesCard state={state} settings={settings} admin={caps.admin} onApply={apply} onVm={(vm) => navigateTo("vm", vm, "summary")} />
      <SettingsCard settings={settings} admin={caps.admin} onSaved={reload} />
      <Card title={t("drs.rules")} note={rules.length || null} flush actions={caps.admin && <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({})}><Plus size={14} aria-hidden="true" />{t("drs.ruleAdd")}</button>}>
        {rules.length === 0 ? <Empty icon={Scale} title={t("drs.rulesNone")} text={t("drs.rulesNoneHelp")} /> : (
          <TableWrap>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("drs.ruleName")}</th><th scope="col">{t("drs.kind")}</th><th scope="col">{t("drs.strength")}</th><th scope="col">VM</th><th scope="col">{t("drs.nodes")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.name}>
                    <th scope="row" className="nx-mono">{r.name}{!r.enabled && <span className="nx-muted"> · {t("drs.disabled")}</span>}</th>
                    <td>{t(`drs.kind.${r.kind}`)}</td>
                    <td><span className="nx-chip" data-tone={r.strength === "must" ? "warning" : "info"}>{t(`drs.${r.strength}`)}</span></td>
                    <td className="nx-wrapcell nx-mono">{r.vms.join(", ")}</td>
                    <td className="nx-wrapcell nx-mono">{withNodes(r.kind) ? r.nodes.join(", ") : <span className="nx-muted">—</span>}</td>
                    <td><div className="nx-ra">{caps.admin && <>
                      <button type="button" className="nx-btn nx-btn--sm" aria-label={t("drs.ruleEditOf", { name: r.name })} onClick={() => setEditing(r)}><Pencil size={14} aria-hidden="true" />{t("drs.edit")}</button>
                      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("drs.ruleDeleteOf", { name: r.name })} onClick={() => removeRule(r.name)}><Trash2 size={14} aria-hidden="true" /></button>
                    </>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <ExceptionsCard behaviours={behaviours} vmNames={vmNames} admin={caps.admin} onSaved={reload} />
      <RuleDrawer rule={editing} vmNames={vmNames} members={members} onClose={() => setEditing(null)} onSaved={reload} />
    </>
  );
}

function StateBanner({ settings, state, admin, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);
  async function resume() {
    setBusy(true);
    try { await saveDrsSettings(settings.mode, settings.threshold); onSaved(); }
    catch (e) { pushToast({ kind: "error", title: t("drs.settingsFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  const moves = (state.moves || []).length;
  const [tone, text] = state.reason === "no cluster" ? ["info", t("drs.noCluster")]
    : settings.mode === "off" ? ["info", t("drs.isOff")]
    : state.paused ? ["danger", t("drs.paused", { reason: state.paused })]
    : moves ? ["warning", t(settings.mode === "automatic" ? "drs.movingN" : "drs.recommendN", { n: moves })]
    : ["success", t("drs.balanced")];
  return (
    <div className="nx-bn" data-tone={tone} role="status"><Scale size={16} aria-hidden="true" />
      <span className="nx-bn-t"><b>{t(`drs.mode.${settings.mode}`)}</b> · {text}{state.leading === false && <><br />{t("drs.notLeading")}</>}</span>
      {state.paused && admin && <button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={resume}>{t("drs.resume")}</button>}
    </div>
  );
}

function MembersCard({ nodes, spread, level }) {
  const t = useT();
  const names = Object.keys(nodes).sort();
  const limit = SPREAD[level];
  return (
    <Card title={t("drs.members")} note={spread == null ? null : t("drs.spreadNote", { spread: Math.round(spread) })} flush>
      <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{limit == null ? t("drs.membersHelpRules") : t("drs.membersHelp", { limit })}</p>
      {names.length === 0 ? <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("drs.membersNone")}</p> : (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("drs.node")}</th><th scope="col">CPU</th><th scope="col">{t("drs.memory")}</th><th scope="col">{t("drs.score")}</th></tr></thead>
            <tbody>
              {names.map((n) => {
                const v = nodes[n];
                return (
                  <tr key={n}>
                    <th scope="row" className="nx-mono">{n}</th>
                    <td className="nx-mono">{pct(v.cpu)}</td>
                    <td className="nx-mono">{pct(v.mem)}</td>
                    <td style={{ minWidth: 160 }}><span className="nx-mono">{pct(v.score)}</span>
                      <span className="nx-track nx-track--wide" aria-hidden="true" style={{ marginTop: "var(--space-1)" }}><span data-tone={scoreTone(v.score)} style={{ width: `${Math.min(100, Math.round(v.score))}%` }} /></span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

function MovesCard({ state, settings, admin, onApply, onVm }) {
  const t = useT();
  const moves = state.moves || [];
  const running = new Set((state.running || []).map((m) => m.vm));
  return (
    <Card title={t("drs.moves")} note={moves.length || null} flush>
      <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t(settings.mode === "automatic" ? "drs.movesHelpAuto" : "drs.movesHelpManual")}</p>
      {moves.length === 0 ? <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("drs.movesNone")}</p> : (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">VM</th><th scope="col">{t("drs.move")}</th><th scope="col">{t("drs.gain")}</th><th scope="col">{t("drs.reason")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {moves.map((m) => (
                <tr key={`${m.vm}-${m.target}`}>
                  <th scope="row"><button type="button" className="nx-lnk nx-mono" onClick={() => onVm(m.vm)}>{m.vm}</button></th>
                  <td className="nx-mono"><span className="nx-inline">{m.source}<ArrowRight size={14} aria-label={t("drs.to")} />{m.target}</span></td>
                  <td className="nx-mono">{m.gain == null ? <span className="nx-chip" data-tone="warning">{t("drs.ruleFix")}</span> : t("drs.points", { n: m.gain })}</td>
                  <td className="nx-wrapcell nx-f-h">{m.reason}</td>
                  <td><div className="nx-ra">
                    {running.has(m.vm) ? <span className="nx-chip" data-tone="info">{t("drs.running")}</span>
                      : admin && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("drs.applyOf", { vm: m.vm })} onClick={() => onApply(m)}><Play size={14} aria-hidden="true" />{t("drs.apply")}</button>}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

function SettingsCard({ settings, admin, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState(settings);
  const [busy, setBusy] = useState(false);
  const { mode, threshold } = settings;
  useEffect(() => { setForm({ mode, threshold }); }, [mode, threshold]); // the page reloads every 15 s: an edit in progress is kept unless the settings changed
  const dirty = form.mode !== settings.mode || form.threshold !== settings.threshold;
  async function save() {
    setBusy(true);
    try { await saveDrsSettings(form.mode, form.threshold); pushToast({ kind: "success", title: t("drs.settingsSaved") }); onSaved(); }
    catch (e) { pushToast({ kind: "error", title: t("drs.settingsFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <Card title={t("drs.settings")} actions={admin && <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" disabled={!dirty || busy} onClick={save}>{t("sso.save")}</button>}>
      <Field label={t("drs.mode")} hint={t(`drs.mode.${form.mode}Help`)}>{(p) => (
        <select {...p} className="nx-inp" value={form.mode} disabled={!admin || busy} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
          {MODES.map((m) => <option key={m} value={m}>{t(`drs.mode.${m}`)}</option>)}
        </select>
      )}</Field>
      <Field label={t("drs.threshold")} hint={SPREAD[form.threshold] == null ? t("drs.level1Help") : t("drs.levelHelp", { limit: SPREAD[form.threshold] })}>{(p) => (
        <select {...p} className="nx-inp" value={form.threshold} disabled={!admin || busy || form.mode === "off"} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })}>
          {[1, 2, 3, 4, 5].map((l) => <option key={l} value={l}>{t(`drs.level${l}`)}</option>)}
        </select>
      )}</Field>
    </Card>
  );
}

function ExceptionsCard({ behaviours, vmNames, admin, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [vm, setVm] = useState("");
  const [behaviour, setBehaviour] = useState("excluded");
  const [busy, setBusy] = useState(false);
  const rows = Object.entries(behaviours || {}).filter(([, b]) => b !== "default").sort(([a], [b]) => a.localeCompare(b));
  async function set(name, value) {
    setBusy(true);
    try { await setDrsBehaviour(name, value); if (name === vm) setVm(""); onSaved(); }
    catch (e) { pushToast({ kind: "error", title: t("drs.behaviourFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <Card title={t("drs.exceptions")} note={rows.length || null} flush>
      <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("drs.exceptionsHelp")}</p>
      {rows.length > 0 && (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">VM</th><th scope="col">{t("drs.behaviour")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {rows.map(([name, b]) => (
                <tr key={name}>
                  <th scope="row" className="nx-mono">{name}</th>
                  <td>{admin ? (
                    <select className="nx-sel" aria-label={t("drs.behaviourOf", { vm: name })} value={b} disabled={busy} onChange={(e) => set(name, e.target.value)}>
                      {["manual", "excluded"].map((x) => <option key={x} value={x}>{t(`drs.b.${x}`)}</option>)}
                    </select>
                  ) : t(`drs.b.${b}`)}</td>
                  <td><div className="nx-ra">{admin && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("drs.behaviourResetOf", { vm: name })} disabled={busy} onClick={() => set(name, "default")}><X size={14} aria-hidden="true" /></button>}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {admin && (
        <form className="nx-inline" style={{ margin: "var(--space-3) var(--space-4)", gap: "var(--space-2)", flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); if (vm) set(vm, behaviour); }}>
          <input className="nx-inp" list="drs-vm-names" aria-label={t("drs.exceptionVm")} placeholder={t("drs.exceptionVm")} value={vm} onChange={(e) => setVm(e.target.value.trim())} style={{ maxWidth: 240 }} />
          <select className="nx-sel" aria-label={t("drs.behaviour")} value={behaviour} onChange={(e) => setBehaviour(e.target.value)}>
            {["excluded", "manual"].map((x) => <option key={x} value={x}>{t(`drs.b.${x}`)}</option>)}
          </select>
          <button type="submit" className="nx-btn nx-btn--sm" disabled={!vm || busy}><Plus size={14} aria-hidden="true" />{t("drs.exceptionAdd")}</button>
          <datalist id="drs-vm-names">{vmNames.map((n) => <option key={n} value={n} />)}</datalist>
        </form>
      )}
    </Card>
  );
}

// A rule: its kind, "must" or "should", its VMs (typed or picked from the list) and, for a node rule, its members.
function RuleDrawer({ rule, vmNames, members, onClose, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (rule) setForm({ name: rule.name || "", kind: rule.kind || "apart", strength: rule.strength || "should", vms: [...(rule.vms || [])], nodes: [...(rule.nodes || [])], enabled: rule.enabled ?? true });
    setTyped(""); setError(null);
  }, [rule]);
  if (!rule || !form) return <SideDrawer open={false} title="" onClose={onClose} />;
  const isNew = !rule.name;
  const nodeChoices = [...new Set([...members, ...form.nodes])].sort();
  const addVm = () => { const v = typed.trim(); if (v && !form.vms.includes(v)) setForm({ ...form, vms: [...form.vms, v] }); setTyped(""); };
  const toggleNode = (n) => setForm((f) => ({ ...f, nodes: f.nodes.includes(n) ? f.nodes.filter((x) => x !== n) : [...f.nodes, n] }));
  async function save() {
    setBusy(true); setError(null);
    try {
      await saveDrsRule({ ...form, nodes: withNodes(form.kind) ? form.nodes : [] });
      pushToast({ kind: "success", title: t("drs.ruleSaved"), message: form.name }); onSaved(); onClose();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return (
    <SideDrawer open title={isNew ? t("drs.ruleNew") : t("drs.ruleEditOf", { name: rule.name })} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !form.name} onClick={save}>{t("sso.save")}</button>
    </>}>
      <Field label={t("drs.ruleName")} hint={isNew ? t("drs.ruleNameHelp") : null}>{(p) => (
        <input {...p} className="nx-inp nx-mono" value={form.name} disabled={!isNew} onChange={(e) => setForm({ ...form, name: e.target.value.trim() })} />
      )}</Field>
      <Field label={t("drs.kind")} hint={t(`drs.kind.${form.kind}Help`)}>{(p) => (
        <select {...p} className="nx-inp" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
          {KINDS.map((k) => <option key={k} value={k}>{t(`drs.kind.${k}`)}</option>)}
        </select>
      )}</Field>
      <fieldset className="nx-stack" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="nx-f-l">{t("drs.strength")}</legend>
        {["should", "must"].map((s) => <label key={s} className="nx-check"><input type="radio" name="drs-strength" checked={form.strength === s} onChange={() => setForm({ ...form, strength: s })} /> {t(`drs.${s}`)} <span className="nx-muted">· {t(`drs.${s}Help`)}</span></label>)}
      </fieldset>
      <fieldset className="nx-stack" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="nx-f-l">VM</legend>
        <p className="nx-f-h" style={{ margin: 0 }}>{t(withNodes(form.kind) ? "drs.vmsHelpNodes" : "drs.vmsHelp")}</p>
        {form.vms.length > 0 && <div className="nx-inline" style={{ flexWrap: "wrap", gap: "var(--space-1)" }}>{form.vms.map((v) => (
          <span key={v} className="nx-chip nx-mono">{v}<button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" style={{ marginLeft: 2, height: 18, width: 18 }} aria-label={t("drs.vmRemove", { vm: v })} onClick={() => setForm({ ...form, vms: form.vms.filter((x) => x !== v) })}><X size={12} aria-hidden="true" /></button></span>
        ))}</div>}
        <div className="nx-inline" style={{ gap: "var(--space-2)" }}>
          <input className="nx-inp" list="drs-rule-vms" aria-label={t("drs.vmAdd")} placeholder={t("drs.vmAdd")} value={typed} onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addVm(); } }} />
          <button type="button" className="nx-btn nx-btn--sm" disabled={!typed.trim()} onClick={addVm}><Plus size={14} aria-hidden="true" />{t("drs.add")}</button>
          <datalist id="drs-rule-vms">{vmNames.filter((n) => !form.vms.includes(n)).map((n) => <option key={n} value={n} />)}</datalist>
        </div>
      </fieldset>
      {withNodes(form.kind) && (
        <fieldset className="nx-stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="nx-f-l">{t("drs.nodes")}</legend>
          {nodeChoices.length === 0 ? <p className="nx-f-h" style={{ margin: 0 }}>{t("drs.membersNone")}</p>
            : nodeChoices.map((n) => <label key={n} className="nx-check"><input type="checkbox" checked={form.nodes.includes(n)} onChange={() => toggleNode(n)} /> <span className="nx-mono">{n}</span></label>)}
        </fieldset>
      )}
      <div className="nx-inline" style={{ justifyContent: "space-between" }}>
        <span className="nx-f-l">{t("drs.enabled")}</span>
        <button type="button" className="nx-sw" role="switch" aria-checked={form.enabled} aria-label={t("drs.enabled")} onClick={() => setForm({ ...form, enabled: !form.enabled })} />
      </div>
      {error && <p className="nx-f-h is-error" role="alert" style={{ margin: 0 }}>{error}</p>}
    </SideDrawer>
  );
}
DrsPage.ownHeader = true;
