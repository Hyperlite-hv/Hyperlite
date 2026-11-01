import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Plus, TriangleAlert } from "lucide-react";
import {
  fetchFirewall, previewFirewall, applyFirewall, confirmFirewall, rollbackFirewall,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import PermissionNotice from "../components/PermissionNotice";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

// The nodes' own firewall (app/core/host_firewall.py): the cluster's rules, each node's, security groups and IP sets.
// Edited as a draft here, checked by nftables in the preview, applied on this node with a rollback timer, and saved for
// the whole cluster once confirmed from this page.

const DELAYS = [60, 120, 300];
const NAME = /^[a-zA-Z][a-zA-Z0-9_-]{0,31}$/;
const PORTS = /^[0-9]{1,5}(-[0-9]{1,5})?(,[0-9]{1,5}(-[0-9]{1,5})?)*$/;
const CIDR = /^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)(\/([0-9]|[12]\d|3[0-2]))?$/;
const blankRule = { action: "accept", protocol: "tcp", port: "", source: "any", comment: "", enabled: true };

function useCountdown(deadline) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!deadline) return undefined;
    const id = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(id);
  }, [deadline]);
  return deadline ? Math.max(0, Math.ceil(deadline - now)) : null;
}

const clone = (doc) => JSON.parse(JSON.stringify(doc));

export default function FirewallPage() {
  const t = useT();
  const isAdmin = capabilities(useAuthStore((s) => s.role)).admin;
  const pushToast = useInfraStore((s) => s.pushToast);
  const clusterNodes = useInfraStore((s) => s.nodes);
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState(null);
  const [editing, setEditing] = useState(null); // {where: "cluster" | node | "group:<name>", index, value}
  const [listEdit, setListEdit] = useState(null); // {kind: "group" | "ipset", index, value}
  const [preview, setPreview] = useState(null);
  const [delay, setDelay] = useState(60);
  const [busy, setBusy] = useState(false);
  const [node, setNode] = useState("");

  const load = useCallback(async () => {
    try { setState(await fetchFirewall()); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { if (isAdmin) load(); }, [load, isAdmin]);
  const pending = state?.pending;
  const left = useCountdown(pending?.deadline);
  useEffect(() => { if (pending && left === 0) { const id = setTimeout(load, 3000); return () => clearTimeout(id); } return undefined; }, [pending, left, load]);

  const doc = useMemo(() => draft ?? state?.document, [draft, state]);
  const nodeNames = useMemo(() => {
    const names = new Set([state?.node, ...Object.keys(doc?.nodes || {}), ...(clusterNodes || []).map((n) => (n.id === "local" ? state?.node : n.name))]);
    return [...names].filter(Boolean).sort();
  }, [state, doc, clusterNodes]);
  useEffect(() => { if (!node && state?.node) setNode(state.node); }, [node, state]);

  if (!isAdmin) return <PermissionNotice requires={t("top.role.admin")} />;
  if (error && !state) return <ErrorState message={error} onRetry={load} />;
  if (!state || !doc) return <Loading />;

  const change = (fn) => { const next = clone(doc); fn(next); setDraft(next); };
  const rulesOf = (d, where) => (where === "cluster" ? d.rules : where.startsWith("group:") ? d.groups.find((g) => g.name === where.slice(6)).rules : ((d.nodes[where] ||= { rules: [] }).rules));
  const locked = !!pending;

  function saveRule(value) {
    const { where, index } = editing;
    change((d) => { const list = rulesOf(d, where); if (index === null) list.push(value); else list[index] = value; });
    setEditing(null);
  }
  function moveRule(where, index, by) {
    change((d) => { const list = rulesOf(d, where); const [r] = list.splice(index, 1); list.splice(index + by, 0, r); });
  }
  async function removeRule(where, index) {
    if (!(await confirmAction({ title: t("hf.deleteRule"), message: t("hf.draftNote"), confirmLabel: t("hf.delete"), danger: true }))) return;
    change((d) => rulesOf(d, where).splice(index, 1));
  }
  function usedBy(kind, name) {
    const refers = (r) => (kind === "group" ? r.group === name : r.source === `+${name}`);
    const places = [];
    if (doc.rules.some(refers)) places.push(t("hf.cluster"));
    for (const [n, c] of Object.entries(doc.nodes)) if (c.rules.some(refers)) places.push(n);
    if (kind === "ipset") for (const g of doc.groups) if (g.rules.some(refers)) places.push(g.name);
    return places;
  }
  async function removeList(kind, index) {
    const item = (kind === "group" ? doc.groups : doc.ipsets)[index];
    const places = usedBy(kind, item.name);
    if (places.length) { pushToast({ kind: "error", title: t("hf.inUse", { name: item.name }), message: places.join(", ") }); return; }
    if (!(await confirmAction({ title: t("hf.deleteNamed", { name: item.name }), message: t("hf.draftNote"), confirmLabel: t("hf.delete"), danger: true }))) return;
    change((d) => (kind === "group" ? d.groups : d.ipsets).splice(index, 1));
  }
  function saveList(value) {
    const { kind, index } = listEdit;
    change((d) => {
      const list = kind === "group" ? d.groups : d.ipsets;
      if (index === null) list.push(kind === "group" ? { ...value, rules: [] } : value);
      else list[index] = kind === "group" ? { ...list[index], comment: value.comment } : value;
    });
    setListEdit(null);
  }
  function setEnabled(on) {
    change((d) => {
      d.enabled = on;
      if (on && d.rules.length === 0) d.rules = clone(state.starter_rules).map((r) => ({ ...blankRule, ...r }));
    });
  }
  async function review() {
    setBusy(true);
    try { setPreview(await previewFirewall(doc)); } catch (e) { pushToast({ kind: "error", title: t("hf.refused"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function apply() {
    setBusy(true);
    try { await applyFirewall(doc, delay); setPreview(null); setDraft(null); await load(); } catch (e) { pushToast({ kind: "error", title: t("hf.notApplied"), message: errorMessage(e) }); await load(); } finally { setBusy(false); }
  }
  async function confirm() {
    setBusy(true);
    try { await confirmFirewall(pending.id); pushToast({ kind: "success", title: t("hf.confirmed") }); await load(); } catch (e) { pushToast({ kind: "error", title: t("hf.confirmFailed"), message: errorMessage(e) }); await load(); } finally { setBusy(false); }
  }
  async function revert() {
    setBusy(true);
    try { await rollbackFirewall(pending.id); pushToast({ kind: "success", title: t("hf.rolledBack") }); await load(); } catch (e) { pushToast({ kind: "error", title: t("hf.rollbackFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }

  const rulesTable = (where, list) => (
    list.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("hf.noRules")}</p> : (
      <TableWrap>
        <table className="nx-table">
          <thead><tr><th scope="col" className="nx-num">#</th><th scope="col">{t("hf.action")}</th><th scope="col">{t("hf.protocol")}</th><th scope="col">{t("hf.port")}</th><th scope="col">{t("hf.source")}</th><th scope="col">{t("hf.comment")}</th><th scope="col"><span className="nx-sr">{t("hf.actions")}</span></th></tr></thead>
          <tbody>
            {list.map((r, i) => (
              <tr key={i} style={r.enabled === false ? { opacity: 0.55 } : undefined}>
                <td className="nx-num nx-mono nx-muted">{i + 1}</td>
                {r.group ? (
                  <td colSpan={4}><Chip>{t("hf.groupChip")}</Chip> <span className="nx-mono">{r.group}</span></td>
                ) : (
                  <>
                    <td><Chip tone={r.action === "accept" ? "success" : "warning"}>{t(`hf.act.${r.action}`)}</Chip></td>
                    <td className="nx-mono">{r.protocol}</td>
                    <td className="nx-mono">{r.port || <span className="nx-muted">{t("hf.allPorts")}</span>}</td>
                    <td className="nx-mono">{r.source === "any" ? <span className="nx-muted">{t("hf.anywhere")}</span> : r.source}</td>
                  </>
                )}
                <td className="nx-wrapcell">{r.enabled === false ? <span className="nx-muted">{t("hf.disabled")} </span> : null}{r.comment || ""}</td>
                <td className="nx-num" style={{ whiteSpace: "nowrap" }}>
                  {!locked && <>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon nx-btn--sm" aria-label={t("hf.up")} disabled={i === 0} onClick={() => moveRule(where, i, -1)}><ArrowUp size={14} aria-hidden="true" /></button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon nx-btn--sm" aria-label={t("hf.down")} disabled={i === list.length - 1} onClick={() => moveRule(where, i, 1)}><ArrowDown size={14} aria-hidden="true" /></button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setEditing({ where, index: i, value: { ...blankRule, ...r } })}>{t("hf.edit")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => removeRule(where, i)}>{t("hf.delete")}</button>
                  </>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    )
  );
  const addButtons = (where, groupsAllowed) => !locked && (
    <div className="nx-bar">
      <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({ where, index: null, value: { ...blankRule } })}><Plus size={14} aria-hidden="true" />{t("hf.addRule")}</button>
      {groupsAllowed && doc.groups.length > 0 && <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({ where, index: null, value: { group: doc.groups[0].name, enabled: true } })}><Plus size={14} aria-hidden="true" />{t("hf.addGroup")}</button>}
    </div>
  );

  return (
    <div className="nx-page">
      <PageHeader title={t("hf.title")} desc={t("hf.desc")} />
      {pending && (
        <div className="nx-bn" data-tone="warning" role="status" aria-live="polite">
          <TriangleAlert size={16} aria-hidden="true" />
          <span className="nx-bn-t"><b>{t("hf.pendingTitle", { n: left ?? 0 })}</b> {t("hf.pendingText", { node: state.node })}</span>
          <button type="button" className="nx-btn nx-btn--sm" onClick={revert} disabled={busy}>{t("hf.rollback")}</button>
          <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" onClick={confirm} disabled={busy || left === 0}>{t("hf.confirm")}</button>
        </div>
      )}
      <Card title={t("hf.options")} note={doc.enabled ? t(state.active ? "hf.activeHere" : "hf.notYetHere", { node: state.node }) : t("hf.off")}>
        <div className="nx-fg">
          <label style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input type="checkbox" checked={doc.enabled} disabled={locked} onChange={(e) => setEnabled(e.target.checked)} />{t("hf.enabled")}
          </label>
          <Field label={t("hf.policy")} hint={t("hf.policyHint")}>
            {(p) => <select {...p} className="nx-inp" value={doc.policy} disabled={locked} onChange={(e) => change((d) => { d.policy = e.target.value; })}><option value="drop">{t("hf.policy.drop")}</option><option value="accept">{t("hf.policy.accept")}</option></select>}
          </Field>
        </div>
        <p className="nx-muted" style={{ marginBottom: 0 }}>{t("hf.pinned")}</p>
      </Card>

      <Card title={t("hf.clusterRules")} note={t("hf.clusterRulesNote")}>
        {addButtons("cluster", true)}
        {rulesTable("cluster", doc.rules)}
      </Card>

      <Card title={t("hf.nodeRules")} note={t("hf.nodeRulesNote")}>
        <div className="nx-bar">
          <Field label={t("hf.node")}>{(p) => <select {...p} className="nx-sel" value={node} onChange={(e) => setNode(e.target.value)}>{nodeNames.map((n) => <option key={n} value={n}>{n}</option>)}</select>}</Field>
        </div>
        {node && addButtons(node, true)}
        {node && rulesTable(node, doc.nodes[node]?.rules || [])}
      </Card>

      <Card title={t("hf.groups")} note={t("hf.groupsNote")}>
        {!locked && <div className="nx-bar"><button type="button" className="nx-btn nx-btn--sm" onClick={() => setListEdit({ kind: "group", index: null, value: { name: "", comment: "" } })}><Plus size={14} aria-hidden="true" />{t("hf.newGroup")}</button></div>}
        {doc.groups.length === 0 ? <Empty title={t("hf.noGroups")} text={t("hf.noGroupsHelp")} /> : doc.groups.map((g, gi) => (
          <section key={g.name} style={{ marginBottom: "var(--space-4)" }} aria-label={g.name}>
            <div className="nx-bar">
              <b className="nx-mono">{g.name}</b>{g.comment && <span className="nx-muted">{g.comment}</span>}
              <span className="nx-sp" />
              {!locked && <>
                <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setListEdit({ kind: "group", index: gi, value: { name: g.name, comment: g.comment } })}>{t("hf.edit")}</button>
                <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => removeList("group", gi)}>{t("hf.delete")}</button>
                <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({ where: `group:${g.name}`, index: null, value: { ...blankRule } })}><Plus size={14} aria-hidden="true" />{t("hf.addRule")}</button>
              </>}
            </div>
            {rulesTable(`group:${g.name}`, g.rules)}
          </section>
        ))}
      </Card>

      <Card title={t("hf.ipsets")} note={t("hf.ipsetsNote")}>
        {!locked && <div className="nx-bar"><button type="button" className="nx-btn nx-btn--sm" onClick={() => setListEdit({ kind: "ipset", index: null, value: { name: "", comment: "", cidrs: [] } })}><Plus size={14} aria-hidden="true" />{t("hf.newIpset")}</button></div>}
        {doc.ipsets.length === 0 ? <Empty title={t("hf.noIpsets")} text={t("hf.noIpsetsHelp")} /> : (
          <TableWrap>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("hf.name")}</th><th scope="col">{t("hf.addresses")}</th><th scope="col">{t("hf.comment")}</th><th scope="col"><span className="nx-sr">{t("hf.actions")}</span></th></tr></thead>
              <tbody>
                {doc.ipsets.map((s, si) => (
                  <tr key={s.name}>
                    <th scope="row" className="nx-mono" style={{ fontWeight: 500 }}>+{s.name}</th>
                    <td className="nx-mono">{s.cidrs.length ? s.cidrs.map((c, k) => <span key={c} style={{ display: "inline-block", whiteSpace: "nowrap" }}>{c}{k < s.cidrs.length - 1 ? ",\u00a0" : ""}</span>) : <span className="nx-muted">—</span>}</td>
                    <td>{s.comment}</td>
                    <td className="nx-num" style={{ whiteSpace: "nowrap" }}>{!locked && <>
                      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setListEdit({ kind: "ipset", index: si, value: { ...s } })}>{t("hf.edit")}</button>
                      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => removeList("ipset", si)}>{t("hf.delete")}</button>
                    </>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      {draft && (
        <div className="nx-bn" role="status">
          <span className="nx-bn-t">{t("hf.draft")}</span>
          <button type="button" className="nx-btn nx-btn--sm" onClick={() => setDraft(null)} disabled={busy}>{t("hf.discard")}</button>
          <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" onClick={review} disabled={busy}>{t("hf.review")}</button>
        </div>
      )}

      {editing && <RuleDrawer value={editing.value} groups={editing.where.startsWith("group:") ? [] : doc.groups.map((g) => g.name)} ipsets={doc.ipsets.map((s) => s.name)} onCancel={() => setEditing(null)} onSave={saveRule} />}
      {listEdit && <ListDrawer kind={listEdit.kind} value={listEdit.value} isNew={listEdit.index === null} taken={new Set((listEdit.kind === "group" ? doc.groups : doc.ipsets).map((x) => x.name))} onCancel={() => setListEdit(null)} onSave={saveList} />}

      <SideDrawer open={!!preview} title={t("hf.reviewTitle")} onClose={() => setPreview(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn" onClick={() => setPreview(null)} disabled={busy}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" onClick={apply} disabled={busy}>{t("hf.apply")}</button>
      </>}>
        {preview && (
          <>
            <p style={{ marginTop: 0 }}>{t("hf.reviewText", { node: state.node })}</p>
            <pre className="nx-code" aria-label={t("hf.ruleset")} style={{ whiteSpace: "pre", overflowX: "auto", maxHeight: "45vh" }}>{preview.ruleset}</pre>
            <Field label={t("hf.delay")} hint={t("hf.delayHint")}>
              {(p) => <select {...p} className="nx-inp" value={delay} onChange={(e) => setDelay(Number(e.target.value))}>{DELAYS.map((d) => <option key={d} value={d}>{t("hn.seconds", { n: d })}</option>)}</select>}
            </Field>
          </>
        )}
      </SideDrawer>
    </div>
  );
}

function RuleDrawer({ value, groups, ipsets, onCancel, onSave }) {
  const t = useT();
  const [v, setV] = useState(value);
  const [tried, setTried] = useState(false);
  const isGroup = v.group !== undefined;
  const set = (k) => (e) => setV({ ...v, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const sourceKind = v.source === "any" || !v.source ? "any" : v.source.startsWith("+") ? "ipset" : "cidr";
  const errors = {};
  if (!isGroup) {
    if (v.port && !["tcp", "udp"].includes(v.protocol)) errors.port = t("hf.errPortProto");
    else if (v.port && !PORTS.test(v.port.replace(/\s/g, ""))) errors.port = t("hf.errPort");
    if (sourceKind === "cidr" && !CIDR.test(v.source)) errors.source = t("hf.errSource");
  }
  const err = (k) => (tried ? errors[k] : undefined);
  function submit(e) {
    e.preventDefault();
    setTried(true);
    if (Object.keys(errors).length) return;
    onSave(isGroup ? { group: v.group, enabled: v.enabled !== false } : { action: v.action, protocol: v.protocol, port: v.port ? v.port.replace(/\s/g, "") : null, source: v.source || "any", comment: v.comment || "", enabled: v.enabled !== false });
  }
  return (
    <SideDrawer open title={t(isGroup ? "hf.groupRef" : "hf.rule")} onClose={onCancel} footer={<>
      <button type="button" className="nx-btn" onClick={onCancel}>{t("action.cancel")}</button>
      <button type="submit" form="fw-rule" className="nx-btn nx-btn--primary">{t("hf.keep")}</button>
    </>}>
      <form id="fw-rule" className="nx-fg nx-fg--1" onSubmit={submit} noValidate>
        {isGroup ? (
          <Field label={t("hf.groupChip")} hint={t("hf.groupRefHint")}>
            {(p) => <select {...p} className="nx-inp" value={v.group} onChange={set("group")}>{groups.map((g) => <option key={g} value={g}>{g}</option>)}</select>}
          </Field>
        ) : (
          <>
            <Field label={t("hf.action")}>
              {(p) => <select {...p} className="nx-inp" value={v.action} onChange={set("action")}>{["accept", "drop", "reject"].map((a) => <option key={a} value={a}>{t(`hf.act.${a}`)}</option>)}</select>}
            </Field>
            <Field label={t("hf.protocol")}>
              {(p) => <select {...p} className="nx-inp" value={v.protocol} onChange={(e) => setV({ ...v, protocol: e.target.value, port: ["tcp", "udp"].includes(e.target.value) ? v.port : "" })}>{["tcp", "udp", "icmp", "all"].map((a) => <option key={a} value={a}>{a === "all" ? t("hf.allProtocols") : a}</option>)}</select>}
            </Field>
            {["tcp", "udp"].includes(v.protocol) && (
              <Field label={t("hf.port")} hint={t("hf.portHint")} error={err("port")}>
                {(p) => <input {...p} className="nx-inp nx-mono" value={v.port || ""} onChange={set("port")} placeholder="22, 8000-8010" autoComplete="off" />}
              </Field>
            )}
            <Field label={t("hf.source")}>
              {(p) => <select {...p} className="nx-inp" value={sourceKind} onChange={(e) => setV({ ...v, source: e.target.value === "any" ? "any" : e.target.value === "ipset" ? `+${ipsets[0] || ""}` : "" })}>
                <option value="any">{t("hf.anywhere")}</option><option value="cidr">{t("hf.sourceCidr")}</option>{ipsets.length > 0 && <option value="ipset">{t("hf.sourceIpset")}</option>}
              </select>}
            </Field>
            {sourceKind === "cidr" && <Field label={t("hf.sourceCidr")} hint={t("hf.sourceHint")} error={err("source")}>{(p) => <input {...p} className="nx-inp nx-mono" value={v.source} onChange={set("source")} placeholder="192.168.1.0/24" autoComplete="off" />}</Field>}
            {sourceKind === "ipset" && <Field label={t("hf.sourceIpset")}>{(p) => <select {...p} className="nx-inp" value={v.source.slice(1)} onChange={(e) => setV({ ...v, source: `+${e.target.value}` })}>{ipsets.map((s) => <option key={s} value={s}>+{s}</option>)}</select>}</Field>}
            <Field label={t("hf.comment")}>{(p) => <input {...p} className="nx-inp" value={v.comment || ""} onChange={set("comment")} maxLength={120} />}</Field>
          </>
        )}
        <label style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}><input type="checkbox" checked={v.enabled !== false} onChange={set("enabled")} />{t("hf.ruleEnabled")}</label>
      </form>
    </SideDrawer>
  );
}

function ListDrawer({ kind, value, isNew, taken, onCancel, onSave }) {
  const t = useT();
  const [name, setName] = useState(value.name);
  const [comment, setComment] = useState(value.comment || "");
  const [cidrs, setCidrs] = useState((value.cidrs || []).join("\n"));
  const [tried, setTried] = useState(false);
  const list = cidrs.split(/[\s,]+/).map((c) => c.trim()).filter(Boolean);
  const errors = {};
  if (isNew && (!NAME.test(name) || taken.has(name))) errors.name = t("hf.errName");
  const bad = list.find((c) => !CIDR.test(c));
  if (kind === "ipset" && bad) errors.cidrs = t("hf.errCidrs", { value: bad });
  function submit(e) {
    e.preventDefault();
    setTried(true);
    if (Object.keys(errors).length) return;
    onSave(kind === "group" ? { name, comment } : { name, comment, cidrs: list });
  }
  return (
    <SideDrawer open title={t(kind === "group" ? (isNew ? "hf.newGroup" : "hf.editGroup") : (isNew ? "hf.newIpset" : "hf.editIpset"))} onClose={onCancel} footer={<>
      <button type="button" className="nx-btn" onClick={onCancel}>{t("action.cancel")}</button>
      <button type="submit" form="fw-list" className="nx-btn nx-btn--primary">{t("hf.keep")}</button>
    </>}>
      <form id="fw-list" className="nx-fg nx-fg--1" onSubmit={submit} noValidate>
        <Field label={t("hf.name")} hint={t("hf.nameHint")} error={tried ? errors.name : undefined}>
          {(p) => <input {...p} className="nx-inp nx-mono" value={name} onChange={(e) => setName(e.target.value)} disabled={!isNew} autoComplete="off" />}
        </Field>
        {kind === "ipset" && (
          <Field label={t("hf.addresses")} hint={t("hf.addressesHint")} error={tried ? errors.cidrs : undefined}>
            {(p) => <textarea {...p} className="nx-inp nx-mono" style={{ height: "8rem", paddingTop: "0.5rem" }} value={cidrs} onChange={(e) => setCidrs(e.target.value)} placeholder={"192.168.1.0/24\n10.8.0.5"} />}
          </Field>
        )}
        <Field label={t("hf.comment")}>{(p) => <input {...p} className="nx-inp" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={120} />}</Field>
      </form>
    </SideDrawer>
  );
}
