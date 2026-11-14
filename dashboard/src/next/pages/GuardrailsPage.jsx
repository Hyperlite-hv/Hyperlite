import { useCallback, useEffect, useState } from "react";
import { Pencil, ShieldCheck } from "lucide-react";
import { fetchGuardrails, fetchProtections, liftProtection, putGuardrailRule, putPoolEnvelope } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { useAccess } from "../lib/access";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

const LIMITS = [["max_vcpu", "gr.maxVcpu"], ["max_memory_mb", "gr.maxMemory"], ["max_disk_gb", "gr.maxDisk"]];
const ENVELOPE = [["max_vms", "vms", "gr.envVms"], ["max_vcpus", "vcpus", "gr.envVcpus"], ["max_memory_mb", "memory_mb", "gr.envMemory"], ["max_disk_gb", "disk_gb", "gr.envDisk"]];

const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
const list = (v) => (v || "").split(",").map((x) => x.trim()).filter(Boolean);
const ruleForm = (r) => ({ name_pattern: r?.name_pattern || "", max_vcpu: r?.max_vcpu ?? "", max_memory_mb: r?.max_memory_mb ?? "", max_disk_gb: r?.max_disk_gb ?? "", storage_pools: (r?.storage_pools || []).join(", "), networks: (r?.networks || []).join(", ") });
const ruleBody = (f) => ({ name_pattern: f.name_pattern.trim() || null, max_vcpu: num(f.max_vcpu), max_memory_mb: num(f.max_memory_mb), max_disk_gb: num(f.max_disk_gb), storage_pools: list(f.storage_pools).length ? list(f.storage_pools) : null, networks: list(f.networks).length ? list(f.networks) : null });

// Guardrails (docs/design/access-2.md, sections 4 and 5): the creation rules of the cluster and of each pool, the
// pools' envelopes with what each holds, and every protection in place. An administrator, or access.manage on the
// whole cluster, changes them; anyone signed in reads them.
export default function GuardrailsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const me = useAccess((s) => s.me);
  const steward = !!me && (me.admin || (me.perimeters || []).some((p) => p.resource_type === "cluster" && p.privileges.includes("access.manage")));
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null); // a pool being edited
  const [cluster, setCluster] = useState(ruleForm(null));

  const load = useCallback(async () => {
    try {
      const [g, protections] = await Promise.all([fetchGuardrails(), fetchProtections()]);
      setData({ ...g, protections });
      setCluster(ruleForm(g.rules.cluster));
      setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function save(fn, title) {
    try { await fn(); pushToast({ kind: "success", title }); await load(); return true; } catch (e) { pushToast({ kind: "error", title: t("gr.saveFailed"), message: errorMessage(e) }); return false; }
  }
  async function lift(p) {
    if (!(await confirmAction({ title: t("gr.liftTitle"), message: t("gr.liftMsg", { what: `${t(`acc.kind.${p.resource_type}`)} ${p.resource_id}` }), confirmLabel: t("gr.lift"), danger: true }))) return;
    save(() => liftProtection(p.resource_type, p.resource_id, p.level), t("gr.lifted"));
  }

  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;
  const set = (k) => (e) => setCluster((f) => ({ ...f, [k]: e.target.value }));

  return (
    <>
      <PageHeader title={t("tab.guardrails")} desc={t("gr.desc")} />
      <Card title={t("gr.clusterRules")} note={t("gr.clusterRulesNote")}>
        <RuleFields t={t} f={cluster} set={set} disabled={!steward} />
        {steward && <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" onClick={() => save(() => putGuardrailRule("cluster", ruleBody(cluster)), t("gr.saved"))}>{t("gr.save")}</button></div>}
      </Card>
      <Card title={t("gr.envelopes")} note={t("gr.envelopesNote")} flush>
        {data.pools.length === 0 ? <Empty icon={ShieldCheck} title={t("gr.noPools")} text={t("gr.noPoolsText")} /> : (
          <TableWrap label={t("gr.envelopes")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.pool")}</th>{ENVELOPE.map(([k, , l]) => <th key={k} scope="col">{t(l)}</th>)}<th scope="col">{t("gr.poolRule")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.pools.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">{p.name}</th>
                    {ENVELOPE.map(([k, u]) => <td key={k}><Usage used={p.usage[u]} max={p.envelope?.[k]} /></td>)}
                    <td>{data.rules[`pool:${p.id}`] ? <Chip tone="accent">{t("gr.ruleOn")}</Chip> : <span className="nx-muted">—</span>}</td>
                    <td><div className="nx-ra">{steward && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("gr.editX", { pool: p.name })} onClick={() => setEditing(p)}><Pencil size={14} aria-hidden="true" /> {t("gr.edit")}</button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <Card title={t("gr.protections")} note={t("gr.protectionsNote")} flush>
        {data.protections.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("gr.noProtections")}</p> : (
          <TableWrap label={t("gr.protections")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("gr.object")}</th><th scope="col">{t("gr.level")}</th><th scope="col">{t("gr.reason")}</th><th scope="col">{t("gr.by")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.protections.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">{t(`acc.kind.${p.resource_type}`)} {p.resource_id}</th>
                    <td><Chip tone={p.level === "freeze" ? "warning" : "accent"}>{t(`gr.level.${p.level}`)}</Chip></td>
                    <td>{p.reason || <span className="nx-muted">—</span>}</td>
                    <td>{p.set_by}</td>
                    <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost" onClick={() => lift(p)}>{t("gr.lift")}</button></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {editing && <PoolDrawer t={t} pool={editing} rule={data.rules[`pool:${editing.id}`]} onClose={() => setEditing(null)} save={save} />}
    </>
  );
}

function Usage({ used, max }) {
  if (max === null || max === undefined) return <span className="nx-mono">{used}</span>;
  const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 100;
  return (
    <span className="hc-usage" title={`${used} / ${max}`}>
      <span className="nx-mono">{used} / {max}</span>
      <span className="hc-usage-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} data-level={pct >= 90 ? "high" : pct >= 70 ? "mid" : "low"} /></span>
    </span>
  );
}

function RuleFields({ t, f, set, disabled }) {
  return (
    <>
      <div className="nx-fg">
        <Field label={t("gr.namePattern")} hint={t("gr.namePatternHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.name_pattern} onChange={set("name_pattern")} disabled={disabled} placeholder="[a-z]+-[0-9]+" />}</Field>
        {LIMITS.map(([k, l]) => <Field key={k} label={t(l)}>{(p) => <input {...p} type="number" min="1" className="nx-inp" value={f[k]} onChange={set(k)} disabled={disabled} />}</Field>)}
      </div>
      <div className="nx-fg">
        <Field label={t("gr.storages")} hint={t("gr.listHint")}>{(p) => <input {...p} className="nx-inp" value={f.storage_pools} onChange={set("storage_pools")} disabled={disabled} />}</Field>
        <Field label={t("gr.networks")} hint={t("gr.listHint")}>{(p) => <input {...p} className="nx-inp" value={f.networks} onChange={set("networks")} disabled={disabled} />}</Field>
      </div>
    </>
  );
}

function PoolDrawer({ t, pool, rule, onClose, save }) {
  const [env, setEnv] = useState(Object.fromEntries(ENVELOPE.map(([k]) => [k, pool.envelope?.[k] ?? ""])));
  const [f, setF] = useState(ruleForm(rule));
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  async function submit() {
    const ok = await save(async () => {
      await putPoolEnvelope(pool.id, Object.fromEntries(ENVELOPE.map(([k]) => [k, num(env[k])])));
      await putGuardrailRule(`pool:${pool.id}`, ruleBody(f));
    }, t("gr.saved"));
    if (ok) onClose();
  }
  return (
    <SideDrawer open title={t("gr.editX", { pool: pool.name })} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" onClick={submit}>{t("gr.save")}</button>
    </>}>
      <fieldset className="nx-fs">
        <legend>{t("gr.envelope")}</legend>
        <p className="nx-muted" style={{ marginTop: 0 }}>{t("gr.envelopeHelp")}</p>
        <div className="nx-fg">
          {ENVELOPE.map(([k, u, l]) => <Field key={k} label={t(l)} hint={t("gr.nowHolds", { n: pool.usage[u] })}>{(p) => <input {...p} type="number" min="0" className="nx-inp" value={env[k]} onChange={(e) => setEnv((x) => ({ ...x, [k]: e.target.value }))} />}</Field>)}
        </div>
      </fieldset>
      <fieldset className="nx-fs">
        <legend>{t("gr.poolRule")}</legend>
        <RuleFields t={t} f={f} set={set} disabled={false} />
      </fieldset>
    </SideDrawer>
  );
}
GuardrailsPage.ownHeader = true;
