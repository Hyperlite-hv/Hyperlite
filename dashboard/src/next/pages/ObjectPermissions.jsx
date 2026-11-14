import { useCallback, useEffect, useState } from "react";
import { fetchObjectAcl, fetchAccessSubjects, fetchAclRoles, fetchCustomRoles, fetchPrivileges, explainAccess, createAcl, deleteAcl } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import PermissionNotice from "../components/PermissionNotice";
import { Card, Chip, Field, Loading, TableWrap } from "../components/ui";
import { localizePrivileges, localizeRoles } from "../lib/apiText";
import { useAccess } from "../lib/access";

// Roles meant for a wide perimeter (a node, a pool, the cluster) and those meant for one object: the wizard shows
// the ones that fit the object first.
const WIDE_ROLES = ["steward", "builder", "observer", "access_delegate", "backup_keeper", "watcher", "maintainer"];
const WIDE_KINDS = new Set(["node", "pool", "cluster"]);

// The Rights tab of a VM, a container or a node (docs/design/access-2.md, section 3): who can do what on it, with
// where each right comes from (the object itself, a pool, its node, the whole cluster); what one account holds on it
// and why; and a short wizard to give a role. Administrators and delegates (access.view / access.manage on the
// object or above it) see it; the server decides what each may change.
function ObjectPermissions({ kind, name }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const me = useAuthStore((s) => s.username);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [denied, setDenied] = useState(false);

  const load = useCallback(async () => {
    try {
      const [acl, roles, customRoles, privileges, subjects] = await Promise.all([
        fetchObjectAcl(kind, name), fetchAclRoles(), fetchCustomRoles(), fetchPrivileges(), fetchAccessSubjects().catch(() => null),
      ]);
      setData({
        acl,
        roles: { ...localizeRoles(t, roles), ...Object.fromEntries(customRoles.map((r) => [r.key, r])) },
        privileges: localizePrivileges(t, privileges),
        subjects,
      });
      setError(null);
    } catch (e) {
      if (e?.status === 403) setDenied(true); else setError(errorMessage(e));
    }
  }, [kind, name, t]);
  useEffect(() => { load(); }, [load]);

  if (denied) return <PermissionNotice requires={t("acc.needView")} />;
  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;

  const subject = (a) => (a.subject_type === "group" ? `${t("sec.group")} ${a.subject_label}` : a.subject_label);
  const source = (a) => {
    if (!a.inherited_type) return <span className="nx-muted">{t("op.direct")}</span>;
    if (a.inherited_type === "cluster") return <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "permissions")}>{t("acc.fromCluster")}</button>;
    if (a.inherited_type === "node") return <span>{t("acc.fromNode", { node: a.inherited_from })}</span>;
    return <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "permissions")}>{t("op.pool", { pool: a.inherited_from })}</button>;
  };
  async function remove(a) {
    if (!(await confirmAction({ title: t("sec.aclDeleteTitle"), message: t("sec.aclDeleteMsg"), confirmLabel: t("sec.remove"), danger: true }))) return;
    try { await deleteAcl(a.id); await load(); } catch (e) { pushToast({ kind: "error", title: t("sec.removeFailed"), message: errorMessage(e) }); }
  }

  return (
    <>
      <Card title={t("acc.whoCan")} flush note={t("acc.whoCanNote")}>
        {data.acl.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("acc.none")}</p> : (
          <TableWrap label={t("acc.whoCan")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.subject")}</th><th scope="col">{t("sec.role")}</th><th scope="col">{t("op.from")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.acl.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">{subject(a)}</th>
                    <td><Chip tone={a.inherited_type ? undefined : "accent"}>{data.roles[a.role]?.label || a.role}</Chip></td>
                    <td>{source(a)}</td>
                    <td><div className="nx-ra">{!a.inherited_type && data.subjects && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("op.removeX", { who: subject(a) })} onClick={() => remove(a)}>{t("sec.remove")}</button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <ExplainCard kind={kind} name={name} data={data} me={me} />
      {data.subjects && <AssignWizard kind={kind} name={name} data={data} onDone={load} />}
    </>
  );
}

// "Test an account": each privilege it holds here and the assignment (or global role) it comes from.
function ExplainCard({ kind, name, data, me }) {
  const t = useT();
  const accounts = data.subjects ? data.subjects.users.map((u) => u.username) : [me];
  const [who, setWho] = useState(accounts[0] || "");
  const [answer, setAnswer] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!who) return undefined;
    let live = true;
    explainAccess(kind, name, who).then((a) => { if (live) { setAnswer(a); setError(null); } }).catch((e) => { if (live) setError(errorMessage(e)); });
    return () => { live = false; };
  }, [kind, name, who]);
  const via = (v) => {
    if (v.global) return t(v.global === "admin" ? "top.role.admin" : "top.role.observer");
    const role = data.roles[v.role]?.label || v.role;
    const where = v.resource_type === "cluster" ? t("acc.wholeCluster") : `${t(`acc.kind.${v.resource_type}`)} ${v.resource_id}`;
    return `${role} · ${where}${v.subject_type === "group" ? ` · ${t("sec.group")} ${v.subject_id}` : ""}`;
  };
  return (
    <Card title={t("acc.test")} note={t("acc.testNote")}>
      <div className="nx-fg">
        <Field label={t("sec.user")}>{(p) => <select {...p} className="nx-inp" value={who} onChange={(e) => setWho(e.target.value)}>{accounts.map((u) => <option key={u} value={u}>{u}</option>)}</select>}</Field>
      </div>
      {error && <p className="nx-err" role="alert">{error}</p>}
      {answer && answer.role === "admin" && <p className="nx-muted">{t("acc.isAdmin", { who: answer.username })}</p>}
      {answer && answer.role !== "admin" && (answer.privileges.length === 0 ? <p className="nx-muted">{t("acc.nothing", { who })}</p> : (
        <TableWrap label={t("acc.test")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("sec.privileges")}</th><th scope="col">{t("acc.grantedBy")}</th></tr></thead>
            <tbody>
              {answer.privileges.map((p) => (
                <tr key={p.privilege}>
                  <th scope="row">{data.privileges[p.privilege] || p.privilege}</th>
                  <td>{p.via.map((v, i) => <Chip key={i}>{via(v)}</Chip>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      ))}
    </Card>
  );
}

// Three short steps instead of three selects: who, which role (described), then a sentence to check before giving.
function AssignWizard({ kind, name, data, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [step, setStep] = useState(1);
  const [f, setF] = useState({ subjectType: "user", subjectId: "", role: "" });
  const [busy, setBusy] = useState(false);
  const subjects = f.subjectType === "user" ? data.subjects.users.map((u) => [u.username, u.username]) : data.subjects.groups.map((g) => [String(g.id), g.name]);
  const wide = WIDE_KINDS.has(kind);
  const ordered = Object.entries(data.roles).sort(([a], [b]) => (WIDE_ROLES.includes(a) === wide ? 0 : 1) - (WIDE_ROLES.includes(b) === wide ? 0 : 1));
  const who = f.subjectType === "group" ? `${t("sec.group")} ${subjects.find(([v]) => v === f.subjectId)?.[1] || ""}` : f.subjectId;
  async function give() {
    setBusy(true);
    try {
      await createAcl({ subject_type: f.subjectType, subject_id: f.subjectId, role: f.role, resource_type: kind, resource_id: name });
      pushToast({ kind: "success", title: t("sec.assigned"), message: name });
      setF({ subjectType: "user", subjectId: "", role: "" });
      setStep(1);
      await onDone();
    } catch (e) { pushToast({ kind: "error", title: t("sec.assignFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("op.add")} note={t("acc.stepOf", { n: step })}>
      <ol className="hc-steps" aria-label={t("op.add")}>
        {["acc.stepWho", "acc.stepRole", "acc.stepCheck"].map((k, i) => <li key={k} aria-current={step === i + 1 ? "step" : undefined} className={step > i + 1 ? "is-done" : undefined}>{t(k)}</li>)}
      </ol>
      {step === 1 && (
        <div className="nx-fg">
          <Field label={t("sec.who")}>{(p) => <select {...p} className="nx-inp" value={f.subjectType} onChange={(e) => setF((x) => ({ ...x, subjectType: e.target.value, subjectId: "" }))}><option value="user">{t("sec.user")}</option><option value="group">{t("sec.group")}</option></select>}</Field>
          <Field label={t("sec.subject")} hint={f.subjectType === "user" ? t("op.usersHint") : null}>{(p) => <select {...p} className="nx-inp" value={f.subjectId} onChange={(e) => setF((x) => ({ ...x, subjectId: e.target.value }))}><option value="">{t("sec.choose")}</option>{subjects.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
        </div>
      )}
      {step === 2 && (
        <fieldset className="nx-fs hc-rolepick">
          <legend>{t("sec.role")}</legend>
          {ordered.map(([k, r]) => (
            <label key={k} className={`hc-roleopt${f.role === k ? " is-on" : ""}`}>
              <input type="radio" name={`role-${kind}-${name}`} value={k} checked={f.role === k} onChange={() => setF((x) => ({ ...x, role: k }))} />
              <span><strong>{r.label}</strong><small>{r.description}</small></span>
            </label>
          ))}
        </fieldset>
      )}
      {step === 3 && <p className="hc-recap">{t("acc.recap", { who, role: data.roles[f.role]?.label || f.role, what: `${t(`acc.kind.${kind}`)} ${name}` })}</p>}
      <div className="nx-fa">
        {step > 1 && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setStep(step - 1)}>{t("acc.back")}</button>}
        {step === 1 && <button type="button" className="nx-btn nx-btn--primary" disabled={!f.subjectId} onClick={() => setStep(2)}>{t("acc.next")}</button>}
        {step === 2 && <button type="button" className="nx-btn nx-btn--primary" disabled={!f.role} onClick={() => setStep(3)}>{t("acc.next")}</button>}
        {step === 3 && <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={give}>{t("sec.assign")}</button>}
      </div>
    </Card>
  );
}

export function VmPermissionsPage({ resource }) { return <ObjectPermissions kind="vm" name={resource.name} />; }
export function ContainerPermissionsPage({ resource }) { return <ObjectPermissions kind="container" name={resource.name} />; }
// A node is known to the permissions by its registered name; this node's page says "local" in its id.
export function NodePermissionsPage({ resource }) {
  const here = useAccess((s) => s.me?.node);
  const name = resource.id === "local" ? here : resource.name;
  return name ? <ObjectPermissions kind="node" name={name} /> : <Loading />;
}
