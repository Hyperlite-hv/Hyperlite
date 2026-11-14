import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Clock, KeyRound, X } from "lucide-react";
import {
  askAccess, createApiToken, createEligibility, decideAccess, deleteEligibility, fetchAccessRequests, fetchAccessSubjects,
  fetchAclRoles, fetchCustomRoles, fetchEligibility, fetchPools,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { useAccess } from "../lib/access";
import { localizeRoles } from "../lib/apiText";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

const STATUS_TONE = { pending: "warning", granted: "success", refused: "danger", ended: undefined, cancelled: undefined };

// Choose a perimeter: the whole cluster, a node, a pool, or one VM or container by name.
export function PerimeterPicker({ value, onChange, pools, nodes }) {
  const t = useT();
  const vms = useInfraStore((s) => s.vms);
  const ids = value.resource_type === "node" ? nodes.map((n) => [n, n])
    : value.resource_type === "pool" ? pools.map((p) => [String(p.id), p.name])
    : value.resource_type === "vm" ? vms.map((v) => [v.name, v.name]) : null;
  return (
    <div className="nx-fg">
      <Field label={t("sec.on")}>{(p) => (
        <select {...p} className="nx-inp" value={value.resource_type} onChange={(e) => onChange({ resource_type: e.target.value, resource_id: e.target.value === "cluster" ? "*" : "" })}>
          {["cluster", "node", "pool", "vm", "container"].map((k) => <option key={k} value={k}>{k === "cluster" ? t("acc.wholeCluster") : t(`acc.kind.${k}`)}</option>)}
        </select>
      )}</Field>
      {value.resource_type !== "cluster" && <Field label={t("sec.resource")}>{(p) => ids
        ? <select {...p} className="nx-inp" value={value.resource_id} onChange={(e) => onChange({ ...value, resource_id: e.target.value })}><option value="">{t("sec.choose")}</option>{ids.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        : <input {...p} className="nx-inp" value={value.resource_id} onChange={(e) => onChange({ ...value, resource_id: e.target.value })} />}</Field>}
    </div>
  );
}

const where = (t, r) => (r.resource_type === "cluster" ? t("acc.wholeCluster") : `${t(`acc.kind.${r.resource_type}`)} ${r.resource_id}`);

// Access (docs/design/access-2.md, sections 6 and 7): the rights an account may take for a while, the requests to
// decide on, the eligibilities a delegate manages, and delegated API tokens.
export default function AccessPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const me = useAccess((s) => s.me);
  const username = useAuthStore((s) => s.username);
  const totp = useAuthStore((s) => s.totpEnabled);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [asking, setAsking] = useState(null);
  const [making, setMaking] = useState(false);

  const load = useCallback(async () => {
    try {
      const [eligibility, requests, roles, customRoles, pools, subjects] = await Promise.all([
        fetchEligibility(), fetchAccessRequests(), fetchAclRoles(), fetchCustomRoles(), fetchPools().catch(() => []), fetchAccessSubjects().catch(() => null),
      ]);
      setData({ eligibility, requests, roles: { ...localizeRoles(t, roles), ...Object.fromEntries(customRoles.map((r) => [r.key, r])) }, pools, subjects });
      setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, [t]);
  useEffect(() => { load(); }, [load]);

  async function act(fn, title) {
    try { await fn(); pushToast({ kind: "success", title }); await load(); useAccess.getState().load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }

  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;
  const role = (k) => data.roles[k]?.label || k;
  const mine = data.eligibility.filter((e) => e.mine);
  const managed = data.eligibility.filter((e) => !e.mine || data.subjects);
  const delegate = !!data.subjects;

  return (
    <>
      <PageHeader title={t("tab.access")} desc={t("ac.desc")} />
      <Card title={t("ac.mine")} note={t("ac.mineNote")} flush>
        {mine.length === 0 ? <Empty icon={Clock} title={t("ac.noneMine")} text={t("ac.noneMineText")} /> : (
          <TableWrap label={t("ac.mine")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.role")}</th><th scope="col">{t("sec.on")}</th><th scope="col">{t("ac.upTo")}</th><th scope="col">{t("ac.approval")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {mine.map((e) => (
                  <tr key={e.id}>
                    <th scope="row"><Chip tone="accent">{role(e.role)}</Chip></th>
                    <td>{where(t, e)}</td>
                    <td>{t("ac.minutes", { n: e.max_minutes })}</td>
                    <td>{t(`ac.approval.${e.approval}`)}</td>
                    <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--primary" onClick={() => setAsking(e)}>{t("ac.ask")}</button></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      <Card title={t("ac.requests")} flush>
        {data.requests.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("ac.noRequests")}</p> : (
          <TableWrap label={t("ac.requests")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.user")}</th><th scope="col">{t("sec.role")}</th><th scope="col">{t("sec.on")}</th><th scope="col">{t("gr.reason")}</th><th scope="col">{t("ac.status")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.requests.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">{r.username}</th>
                    <td>{role(r.role)}</td>
                    <td>{where(t, r)}</td>
                    <td>{r.reason}</td>
                    <td><Chip tone={STATUS_TONE[r.status]}>{t(`ac.status.${r.status}`)}</Chip>{r.status === "granted" && r.expires_at && <small className="nx-muted"> {t("ac.until", { d: formatDateTime(r.expires_at, lang) })}</small>}</td>
                    <td><div className="nx-ra">
                      {r.status === "pending" && r.may_decide && <>
                        <button type="button" className="nx-btn nx-btn--primary" onClick={() => act(() => decideAccess(r.id, "approve"), t("ac.approved"))}><Check size={14} aria-hidden="true" /> {t("ac.approve")}</button>
                        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => act(() => decideAccess(r.id, "refuse"), t("ac.refused"))}><X size={14} aria-hidden="true" /> {t("ac.refuse")}</button>
                      </>}
                      {r.username === username && (r.status === "pending" || r.status === "granted") && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => act(() => decideAccess(r.id, "cancel"), t(r.status === "pending" ? "ac.withdrawn" : "ac.givenBack"))}>{t(r.status === "pending" ? "ac.withdraw" : "ac.giveBack")}</button>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      {delegate && (
        <Card title={t("ac.eligibility")} note={t("ac.eligibilityNote")} actions={<button type="button" className="nx-btn nx-btn--primary" onClick={() => setMaking(true)}>{t("ac.makeEligible")}</button>} flush>
          {managed.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("ac.noEligibility")}</p> : (
            <TableWrap label={t("ac.eligibility")}>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("sec.subject")}</th><th scope="col">{t("sec.role")}</th><th scope="col">{t("sec.on")}</th><th scope="col">{t("ac.upTo")}</th><th scope="col">{t("ac.approval")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {managed.map((e) => (
                    <tr key={e.id}>
                      <th scope="row">{e.subject_type === "group" ? `${t("sec.group")} ${data.subjects.groups.find((g) => String(g.id) === e.subject_id)?.name || e.subject_id}` : e.subject_id}</th>
                      <td>{role(e.role)}</td>
                      <td>{where(t, e)}</td>
                      <td>{t("ac.minutes", { n: e.max_minutes })}</td>
                      <td>{t(`ac.approval.${e.approval}`)}</td>
                      <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost" onClick={async () => { if (await confirmAction({ title: t("ac.removeEligibility"), message: t("ac.removeEligibilityMsg"), confirmLabel: t("sec.remove"), danger: true })) act(() => deleteEligibility(e.id), t("ac.removed")); }}>{t("sec.remove")}</button></div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      )}

      <DelegatedToken roles={data.roles} pools={data.pools} nodes={me?.nodes || []} />

      {asking && <AskDrawer eligibility={asking} role={role(asking.role)} totp={totp} onClose={() => setAsking(null)} onDone={(granted) => { setAsking(null); act(() => Promise.resolve(), t(granted ? "ac.granted" : "ac.asked")); }} />}
      {making && <EligibilityDrawer data={data} nodes={me?.nodes || []} onClose={() => setMaking(false)} onDone={() => { setMaking(false); act(() => Promise.resolve(), t("ac.madeEligible")); }} />}
    </>
  );
}
AccessPage.ownHeader = true;

function AskDrawer({ eligibility, role, totp, onClose, onDone }) {
  const t = useT();
  const [f, setF] = useState({ minutes: Math.min(60, eligibility.max_minutes), reason: "", code: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    try {
      const r = await askAccess({ eligibility_id: eligibility.id, minutes: Number(f.minutes), reason: f.reason.trim(), code: f.code || null });
      onDone(r.status === "granted");
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return (
    <SideDrawer open title={t("ac.askX", { role })} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !f.reason.trim() || (totp && !f.code)} onClick={submit}>{t(eligibility.approval === "auto" ? "ac.take" : "ac.send")}</button>
    </>}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t(eligibility.approval === "auto" ? "ac.autoHelp" : "ac.approvalHelp")}</p>
      <Field label={t("ac.forMinutes")} hint={t("ac.minutesMax", { n: eligibility.max_minutes })}>{(p) => <input {...p} type="number" min="5" max={eligibility.max_minutes} className="nx-inp" value={f.minutes} onChange={(e) => setF((x) => ({ ...x, minutes: e.target.value }))} />}</Field>
      <Field label={t("gr.reason")}>{(p) => <input {...p} className="nx-inp" maxLength={500} value={f.reason} onChange={(e) => setF((x) => ({ ...x, reason: e.target.value }))} />}</Field>
      {totp && <Field label={t("ac.code")}>{(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" autoComplete="one-time-code" value={f.code} onChange={(e) => setF((x) => ({ ...x, code: e.target.value.trim() }))} />}</Field>}
      {error && <p className="nx-err" role="alert">{error}</p>}
    </SideDrawer>
  );
}

function EligibilityDrawer({ data, nodes, onClose, onDone }) {
  const t = useT();
  const [f, setF] = useState({ subject_type: "user", subject_id: "", role: "operator", max_minutes: 60, approval: "approval" });
  const [where, setWhere] = useState({ resource_type: "cluster", resource_id: "*" });
  const [error, setError] = useState(null);
  const subjects = f.subject_type === "user" ? data.subjects.users.map((u) => [u.username, u.username]) : data.subjects.groups.map((g) => [String(g.id), g.name]);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  async function submit() {
    try { await createEligibility({ ...f, ...where, max_minutes: Number(f.max_minutes) }); onDone(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <SideDrawer open title={t("ac.makeEligible")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.subject_id || !where.resource_id} onClick={submit}>{t("ac.makeEligible")}</button>
    </>}>
      <div className="nx-fg">
        <Field label={t("sec.who")}>{(p) => <select {...p} className="nx-inp" value={f.subject_type} onChange={(e) => setF((x) => ({ ...x, subject_type: e.target.value, subject_id: "" }))}><option value="user">{t("sec.user")}</option><option value="group">{t("sec.group")}</option></select>}</Field>
        <Field label={t("sec.subject")}>{(p) => <select {...p} className="nx-inp" value={f.subject_id} onChange={set("subject_id")}><option value="">{t("sec.choose")}</option>{subjects.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
      </div>
      <Field label={t("sec.role")} hint={data.roles[f.role]?.description}>{(p) => <select {...p} className="nx-inp" value={f.role} onChange={set("role")}>{Object.entries(data.roles).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}</select>}</Field>
      <PerimeterPicker value={where} onChange={setWhere} pools={data.pools} nodes={nodes} />
      <div className="nx-fg">
        <Field label={t("ac.upTo")} hint={t("ac.inMinutes")}>{(p) => <input {...p} type="number" min="5" max="44640" className="nx-inp" value={f.max_minutes} onChange={set("max_minutes")} />}</Field>
        <Field label={t("ac.approval")}>{(p) => <select {...p} className="nx-inp" value={f.approval} onChange={set("approval")}><option value="approval">{t("ac.approval.approval")}</option><option value="auto">{t("ac.approval.auto")}</option></select>}</Field>
      </div>
      {error && <p className="nx-err" role="alert">{error}</p>}
    </SideDrawer>
  );
}

// A token that acts with one role on one perimeter, within its owner's rights: for a pipeline that creates VMs in
// one pool and nothing else. The token is shown once.
function DelegatedToken({ roles, pools, nodes }) {
  const t = useT();
  const [f, setF] = useState({ name: "", days: "90", role: "operator" });
  const [where, setWhere] = useState({ resource_type: "pool", resource_id: "" });
  const [made, setMade] = useState(null);
  const [error, setError] = useState(null);
  const ordered = useMemo(() => Object.entries(roles), [roles]);
  async function submit() {
    try {
      setMade(await createApiToken(f.name.trim(), f.days ? Number(f.days) : null, { role: f.role, ...where }));
      setError(null);
      setF((x) => ({ ...x, name: "" }));
    } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <Card title={t("ac.token")} note={t("ac.tokenNote")}>
      <div className="nx-fg">
        <Field label={t("ac.tokenName")}>{(p) => <input {...p} className="nx-inp" maxLength={100} value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} placeholder="terraform-ci" />}</Field>
        <Field label={t("ac.tokenDays")}>{(p) => <input {...p} type="number" min="1" max="3650" className="nx-inp" value={f.days} onChange={(e) => setF((x) => ({ ...x, days: e.target.value }))} />}</Field>
        <Field label={t("sec.role")}>{(p) => <select {...p} className="nx-inp" value={f.role} onChange={(e) => setF((x) => ({ ...x, role: e.target.value }))}>{ordered.map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}</select>}</Field>
      </div>
      <PerimeterPicker value={where} onChange={setWhere} pools={pools} nodes={nodes} />
      <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim() || !where.resource_id} onClick={submit}><KeyRound size={14} aria-hidden="true" /> {t("ac.makeToken")}</button></div>
      {error && <p className="nx-err" role="alert">{error}</p>}
      {made && <div className="hc-token-once"><p>{t("ac.tokenOnce")}</p><code className="nx-mono">{made.token}</code></div>}
    </Card>
  );
}
