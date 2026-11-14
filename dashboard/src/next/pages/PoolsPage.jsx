import { useCallback, useEffect, useState } from "react";
import { Boxes, Plus, Trash2 } from "lucide-react";
import { addPoolMember, setPoolRule, createPool, deletePool, fetchGuardrails, fetchMetaList, fetchPools, fetchProtections, removePoolMember } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { useAccess } from "../lib/access";
import { ErrorState } from "../components/States";
import NotesCard, { TagChips } from "../components/NotesCard";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";
import { PoolPermissionsPanel } from "./ObjectPermissions";

// Pools as objects of their own (docs/design/access-2.md): a group of VMs, wherever they run, with its members, its
// tags and notes, its envelope, its protections and who may act on it. Rights given on a pool hold on its VMs.
export default function PoolsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const me = useAccess((s) => s.me);
  const admin = !!me?.admin;
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [creating, setCreating] = useState(false);
  const [f, setF] = useState({ name: "", description: "" });

  const load = useCallback(async () => {
    try {
      const [pools, guard, protections, meta] = await Promise.all([fetchPools(), fetchGuardrails().catch(() => ({ pools: [] })), fetchProtections().catch(() => []), fetchMetaList("pool").catch(() => [])]);
      const usage = Object.fromEntries(guard.pools.map((p) => [p.id, p]));
      const tags = Object.fromEntries(meta.map((m) => [m.name, m.tags]));
      setData(pools.map((p) => ({ ...p, guard: usage[p.id], tags: tags[String(p.id)] || [], protections: protections.filter((x) => x.resource_type === "pool" && x.resource_id === String(p.id)) })));
      setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function run(fn, title) {
    try { await fn(); pushToast({ kind: "success", title }); await load(); return true; } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); return false; }
  }
  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;
  const pool = open && data.find((p) => p.id === open);

  return (
    <>
      <PageHeader title={t("tab.pools")} count={data.length} desc={t("pl.desc")} actions={admin && <button type="button" className="nx-btn nx-btn--primary" onClick={() => setCreating(true)}><Plus size={14} aria-hidden="true" /> {t("pl.create")}</button>} />
      <Card flush>
        {data.length === 0 ? <Empty icon={Boxes} title={t("gr.noPools")} text={t("pl.emptyText")} /> : (
          <TableWrap label={t("tab.pools")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.pool")}</th><th scope="col">{t("pl.members")}</th><th scope="col">{t("gr.envelope")}</th><th scope="col">{t("gr.protections")}</th><th scope="col">{t("pl.tags")}</th></tr></thead>
              <tbody>
                {data.map((p) => (
                  <tr key={p.id} className="is-clickable" onClick={() => setOpen(p.id)}>
                    <th scope="row"><button type="button" className="nx-lnk" onClick={(e) => { e.stopPropagation(); setOpen(p.id); }}>{p.name}</button>{p.description && <small className="nx-muted"> · {p.description}</small>}</th>
                    <td>{p.vms.length + (p.dynamic_vms?.length || 0)}{p.rule && <> <Chip tone="accent">{t("pl.dynamic")}</Chip></>}</td>
                    <td>{p.guard?.envelope ? <Chip tone="accent">{t("pl.envelopeOn")}</Chip> : <span className="nx-muted">—</span>}</td>
                    <td>{p.protections.length ? p.protections.map((x) => <Chip key={x.id} tone={x.level === "freeze" ? "warning" : "accent"}>{t(`gr.level.${x.level}`)}</Chip>) : <span className="nx-muted">—</span>}</td>
                    <td><TagChips tags={p.tags} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      {pool && (
        <SideDrawer open title={t("pl.poolX", { pool: pool.name })} onClose={() => setOpen(null)}>
          <Card title={t("pl.members")} flush actions={admin && <AddMember t={t} vms={vms.filter((v) => !pool.vms.includes(v.name))} onAdd={(name) => run(() => addPoolMember(pool.id, name), t("pl.added"))} />}>
            {pool.vms.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("pl.noMembers")}</p> : (
              <TableWrap label={t("pl.members")}>
                <table className="nx-table">
                  <tbody>
                    {pool.vms.map((name) => (
                      <tr key={name}>
                        <th scope="row">{name}</th>
                        <td><div className="nx-ra">{admin && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("pl.removeX", { vm: name })} onClick={async () => { if (await confirmAction({ title: t("pl.removeTitle"), message: t("pl.removeMsg", { vm: name, pool: pool.name }), confirmLabel: t("sec.remove") })) run(() => removePoolMember(pool.id, name), t("pl.removed")); }}>{t("sec.remove")}</button>}</div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </Card>
          {(admin || pool.rule) && <PoolRule t={t} pool={pool} admin={admin} onSave={(rule) => run(() => setPoolRule(pool.id, rule), t("pl.ruleSaved"))} />}
          <NotesCard kind="pool" name={String(pool.id)} canEdit />
          <PoolPermissionsPanel poolId={pool.id} />
          {admin && <div className="nx-fa"><button type="button" className="nx-btn nx-btn--danger" onClick={async () => { if (await confirmAction({ title: t("pl.deleteTitle"), message: t("pl.deleteMsg", { pool: pool.name }), confirmLabel: t("pl.delete"), danger: true }) && await run(() => deletePool(pool.id), t("pl.deleted"))) setOpen(null); }}><Trash2 size={14} aria-hidden="true" /> {t("pl.delete")}</button></div>}
        </SideDrawer>
      )}

      {creating && (
        <SideDrawer open title={t("pl.create")} onClose={() => setCreating(false)} footer={<>
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setCreating(false)}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim()} onClick={async () => { if (await run(() => createPool(f.name.trim(), f.description.trim()), t("pl.created"))) { setCreating(false); setF({ name: "", description: "" }); } }}>{t("pl.create")}</button>
        </>}>
          <Field label={t("pl.name")}>{(p) => <input {...p} className="nx-inp" maxLength={100} value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} />}</Field>
          <Field label={t("pl.description")}>{(p) => <input {...p} className="nx-inp" maxLength={500} value={f.description} onChange={(e) => setF((x) => ({ ...x, description: e.target.value }))} />}</Field>
        </SideDrawer>
      )}
    </>
  );
}
PoolsPage.ownHeader = true;

function AddMember({ t, vms, onAdd }) {
  const [vm, setVm] = useState("");
  return (
    <span className="hc-addmember">
      <select className="nx-inp" aria-label={t("pl.addVm")} value={vm} onChange={(e) => setVm(e.target.value)}><option value="">{t("pl.addVm")}</option>{vms.map((v) => <option key={`${v.node}/${v.name}`} value={v.name}>{v.name}</option>)}</select>
      <button type="button" className="nx-btn" disabled={!vm} onClick={() => { onAdd(vm); setVm(""); }}>{t("pl.add")}</button>
    </span>
  );
}

// A dynamic pool: the VMs carrying a tag, or whose name matches a pattern, belong to it as its members do.
function PoolRule({ t, pool, admin, onSave }) {
  const [f, setF] = useState({ tag: pool.rule?.tag || "", name_pattern: pool.rule?.name_pattern || "" });
  return (
    <Card title={t("pl.rule")} note={t("pl.ruleNote")}>
      <div className="nx-fg">
        <Field label={t("pl.ruleTag")}>{(p) => <input {...p} className="nx-inp nx-mono" maxLength={32} value={f.tag} disabled={!admin} onChange={(e) => setF((x) => ({ ...x, tag: e.target.value }))} placeholder="prod" />}</Field>
        <Field label={t("pl.ruleName")} hint={t("gr.namePatternHint")}>{(p) => <input {...p} className="nx-inp nx-mono" maxLength={200} value={f.name_pattern} disabled={!admin} onChange={(e) => setF((x) => ({ ...x, name_pattern: e.target.value }))} placeholder="web-.*" />}</Field>
      </div>
      {pool.dynamic_vms?.length > 0 && <p className="nx-muted">{t("pl.ruleTakes", { n: pool.dynamic_vms.length })} <span className="nx-mono">{pool.dynamic_vms.join(", ")}</span></p>}
      {admin && <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" onClick={() => onSave({ tag: f.tag.trim() || null, name_pattern: f.name_pattern.trim() || null })}>{t("gr.save")}</button></div>}
    </Card>
  );
}
