import { useCallback, useEffect, useState } from "react";
import { Copy, Gauge, Minus, Plus, Trash2 } from "lucide-react";
import { createVmGroup, deleteVmGroup, fetchNetworks, fetchPools, fetchTemplates, fetchVmGroups, scaleVmGroup, setVmGroupScaling, stopVmGroupScaling } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { refreshInventory } from "../lib/inventory";
import { ErrorState } from "../components/States";
import { TagChips } from "../components/NotesCard";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer } from "../components/ui";

// VM groups (docs/design/governance.md, section 13): N identical VMs from one template, named <prefix>-<n>; raising
// the count deploys the missing ones, lowering it deletes the highest-numbered stopped ones (to the recycle bin).
export default function VmGroupsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [making, setMaking] = useState(false);
  const [scaling, setScaling] = useState(null);
  const load = useCallback(() => fetchVmGroups().then((r) => { setRows(r); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  async function scale(g, count) {
    try {
      const r = await scaleVmGroup(g.id, count);
      pushToast({ kind: "success", title: t("vg.scaled", { n: count }), message: [r.made.length && t("vg.made", { v: r.made.join(", ") }), r.removed.length && t("vg.removed", { v: r.removed.join(", ") })].filter(Boolean).join(" · ") || undefined });
    } catch (e) {
      if (e.status === 409 && (await confirmAction({ title: t("vg.forceTitle"), message: errorMessage(e), confirmLabel: t("vg.force"), danger: true }))) {
        try { await scaleVmGroup(g.id, count, true); } catch (x) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(x) }); }
      } else if (e.status !== 409) pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) });
    }
    load(); refreshInventory().catch(() => {});
  }
  if (error && !rows) return <ErrorState message={error} onRetry={load} />;
  if (!rows) return <Loading />;
  return (
    <>
      <PageHeader title={t("tab.vmgroups")} count={rows.length} desc={t("vg.desc")} actions={<button type="button" className="nx-btn nx-btn--primary" onClick={() => setMaking(true)}><Plus size={14} aria-hidden="true" /> {t("vg.new")}</button>} />
      {rows.length === 0 ? <Card><Empty icon={Copy} title={t("vg.none")} text={t("vg.noneText")} /></Card> : rows.map((g) => (
        <Card key={g.id} title={g.name} note={`${t("vg.from", { tpl: g.template })} · ${g.prefix}-N`} actions={<><button type="button" className="nx-btn nx-btn--sm" onClick={() => setScaling(g)}><Gauge size={14} aria-hidden="true" /> {g.scaling?.enabled ? t("as.onShort", { min: g.scaling.min_count, max: g.scaling.max_count }) : t("as.button")}</button><button type="button" className="nx-btn nx-btn--ghost" aria-label={t("vg.forget")} onClick={async () => { if (await confirmAction({ title: t("vg.forget"), message: t("vg.forgetMsg"), confirmLabel: t("vg.forget") })) { await deleteVmGroup(g.id); load(); } }}><Trash2 size={14} aria-hidden="true" /></button></>}>
          <div className="hc-vg-count">
            <button type="button" className="nx-btn" aria-label={t("vg.less")} disabled={g.members.length === 0} onClick={() => scale(g, g.members.length - 1)}><Minus size={14} aria-hidden="true" /></button>
            <strong>{g.members.length}</strong>
            <button type="button" className="nx-btn" aria-label={t("vg.more")} onClick={() => scale(g, g.members.length + 1)}><Plus size={14} aria-hidden="true" /></button>
            <span className="nx-muted">{t("vg.wanted", { n: g.count })}</span>
            <TagChips tags={g.tags} />
          </div>
          <p>{g.members.map((m) => <Chip key={m.name} tone={m.running ? "success" : undefined}>{m.name}</Chip>)}</p>
          {g.scaling?.last_action && <p className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{t("as.last", { what: g.scaling.last_action, why: g.scaling.last_reason || "" })}</p>}
        </Card>
      ))}
      {scaling && <ScalingDrawer t={t} group={scaling} onClose={() => setScaling(null)} onDone={() => { setScaling(null); load(); }} />}
      {making && <GroupDrawer t={t} onClose={() => setMaking(false)} onDone={() => { setMaking(false); load(); refreshInventory().catch(() => {}); pushToast({ kind: "success", title: t("vg.created") }); }} />}
    </>
  );
}
VmGroupsPage.ownHeader = true;

function GroupDrawer({ t, onClose, onDone }) {
  const [f, setF] = useState({ name: "", template: "", prefix: "", count: 2, network: "", linked: true, pool_id: "", tags: "" });
  const [opts, setOpts] = useState({ templates: [], networks: [], pools: [] });
  const [error, setError] = useState(null);
  useEffect(() => { Promise.all([fetchTemplates().catch(() => []), fetchNetworks().catch(() => []), fetchPools().catch(() => [])]).then(([templates, networks, pools]) => setOpts({ templates, networks, pools })); }, []);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  async function submit() {
    try {
      await createVmGroup({ ...f, count: Number(f.count), network: f.network || null, pool_id: f.pool_id ? Number(f.pool_id) : null, tags: f.tags.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean) });
      onDone();
    } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <SideDrawer open title={t("vg.new")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim() || !f.template || !f.prefix} onClick={submit}>{t("vg.create")}</button>
    </>}>
      <Field label={t("pl.name")}>{(p) => <input {...p} className="nx-inp" value={f.name} onChange={set("name")} />}</Field>
      <Field label={t("vg.template")}>{(p) => <select {...p} className="nx-inp" value={f.template} onChange={set("template")}><option value="">{t("sec.choose")}</option>{opts.templates.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}</select>}</Field>
      <div className="nx-fg">
        <Field label={t("vg.prefix")} hint={t("vg.prefixHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.prefix} onChange={set("prefix")} placeholder="runner" />}</Field>
        <Field label={t("vg.count")}>{(p) => <input {...p} type="number" min="0" max="100" className="nx-inp" value={f.count} onChange={set("count")} />}</Field>
      </div>
      <div className="nx-fg">
        <Field label={t("sec.network")}>{(p) => <select {...p} className="nx-inp" value={f.network} onChange={set("network")}><option value="">{t("vg.templateNet")}</option>{[...new Set(opts.networks.map((n) => n.name))].map((n) => <option key={n} value={n}>{n}</option>)}</select>}</Field>
        <Field label={t("sec.pool")}>{(p) => <select {...p} className="nx-inp" value={f.pool_id} onChange={set("pool_id")}><option value="">—</option>{opts.pools.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>}</Field>
      </div>
      <Field label={t("pl.tags")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.tags} onChange={set("tags")} placeholder="ci, runner" />}</Field>
      <label className="nx-check"><input type="checkbox" checked={f.linked} onChange={set("linked")} /> {t("vg.linked")}</label>
      {error && <p className="nx-err" role="alert">{error}</p>}
    </SideDrawer>
  );
}

// Autoscaling (docs/design/console-2.md, section 5): the group follows the average CPU of its running members.
function ScalingDrawer({ t, group, onClose, onDone }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const s0 = group.scaling;
  const [f, setF] = useState({ enabled: s0 ? s0.enabled : true, min_count: s0?.min_count ?? 1, max_count: s0?.max_count ?? Math.max(3, group.members.length), up_pct: s0?.up_pct ?? 75, down_pct: s0?.down_pct ?? 25, window_min: s0?.window_min ?? 10, step: s0?.step ?? 1, cooldown_min: s0?.cooldown_min ?? 15 });
  const [error, setError] = useState(null);
  const num = (k) => (e) => setF((x) => ({ ...x, [k]: Number(e.target.value) }));
  async function save() {
    try { await setVmGroupScaling(group.id, f); pushToast({ kind: "success", title: t("as.saved"), message: group.name }); onDone(); } catch (e) { setError(errorMessage(e)); }
  }
  async function stop() {
    try { await stopVmGroupScaling(group.id); onDone(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <SideDrawer open title={t("as.title", { name: group.name })} onClose={onClose} footer={<>
      {s0 && <button type="button" className="nx-btn nx-btn--ghost" onClick={stop}>{t("as.remove")}</button>}
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" onClick={save}>{t("as.save")}</button>
    </>}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("as.note")}</p>
      {error && <p className="nx-err" role="alert">{error}</p>}
      <label className="nx-check"><input type="checkbox" checked={f.enabled} onChange={(e) => setF((x) => ({ ...x, enabled: e.target.checked }))} /> {t("as.enabled")}</label>
      <div className="nx-fg">
        <Field label={t("as.min")}>{(p) => <input {...p} type="number" className="nx-inp" min={0} max={100} value={f.min_count} onChange={num("min_count")} />}</Field>
        <Field label={t("as.max")}>{(p) => <input {...p} type="number" className="nx-inp" min={0} max={100} value={f.max_count} onChange={num("max_count")} />}</Field>
        <Field label={t("as.up")} unit="%">{(p) => <input {...p} type="number" className="nx-inp" min={1} max={100} value={f.up_pct} onChange={num("up_pct")} />}</Field>
        <Field label={t("as.down")} unit="%">{(p) => <input {...p} type="number" className="nx-inp" min={1} max={100} value={f.down_pct} onChange={num("down_pct")} />}</Field>
        <Field label={t("as.window")} unit="min">{(p) => <input {...p} type="number" className="nx-inp" min={5} max={120} value={f.window_min} onChange={num("window_min")} />}</Field>
        <Field label={t("as.step")}>{(p) => <input {...p} type="number" className="nx-inp" min={1} max={10} value={f.step} onChange={num("step")} />}</Field>
        <Field label={t("as.cooldown")} unit="min">{(p) => <input {...p} type="number" className="nx-inp" min={5} max={1440} value={f.cooldown_min} onChange={num("cooldown_min")} />}</Field>
      </div>
      {s0?.history?.length > 0 && (
        <fieldset className="nx-fs">
          <legend>{t("as.history")}</legend>
          <ul className="hc-dcard-list">{s0.history.map((h) => <li key={h.at}><span className="nx-mono">{h.action}</span><small>{new Date(h.at).toLocaleString()} · {h.reason}</small></li>)}</ul>
        </fieldset>
      )}
    </SideDrawer>
  );
}
