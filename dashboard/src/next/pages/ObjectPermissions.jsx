import { useCallback, useEffect, useState } from "react";
import { fetchObjectAcl, fetchUsers, fetchGroups, fetchAclRoles, fetchCustomRoles, createAcl, deleteAcl } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import PermissionNotice from "../components/PermissionNotice";
import { Card, Chip, Field, Loading, TableWrap } from "../components/ui";

// Who may act on one VM or container: its own assignments (added and removed here) and, for a VM, the ones it
// inherits from its pools (changed on the Permissions page, where the pool is managed). Administrators see and
// do everything, observers only read; neither needs an assignment, so they are summed up in one line.
function ObjectPermissions({ kind, name }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [f, setF] = useState({ subjectType: "user", subjectId: "", role: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [acl, users, groups, roles, customRoles] = await Promise.all([fetchObjectAcl(kind, name), fetchUsers(), fetchGroups(), fetchAclRoles(), fetchCustomRoles()]);
      setData({ acl, users, groups, roles: { ...roles, ...Object.fromEntries(customRoles.map((r) => [r.key, r])) } });
      setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, [kind, name]);
  useEffect(() => { if (caps.admin) load(); }, [load, caps.admin]);

  if (!caps.admin) return <PermissionNotice requires={t("top.role.admin")} />;
  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;

  const role = data.roles[f.role] ? f.role : Object.keys(data.roles)[0];
  // Administrators already hold every right; assignments add rights on top of an observer account.
  const scoped = data.users.filter((u) => u.role !== "admin");
  const subjects = f.subjectType === "user" ? scoped.map((u) => [u.username, u.username]) : data.groups.map((g) => [String(g.id), g.name]);
  const byRole = (r) => data.users.filter((u) => u.role === r).map((u) => u.username).join(", ") || "—";

  async function add() {
    setBusy(true);
    try {
      await createAcl({ subject_type: f.subjectType, subject_id: f.subjectId, role, resource_type: kind, resource_id: name });
      pushToast({ kind: "success", title: t("sec.assigned"), message: name });
      setF((x) => ({ ...x, subjectId: "" }));
      await load();
    } catch (e) { pushToast({ kind: "error", title: t("sec.assignFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function remove(a) {
    if (!(await confirmAction({ title: t("sec.aclDeleteTitle"), message: t("sec.aclDeleteMsg"), confirmLabel: t("sec.remove"), danger: true }))) return;
    try { await deleteAcl(a.id); await load(); } catch (e) { pushToast({ kind: "error", title: t("sec.removeFailed"), message: errorMessage(e) }); }
  }
  const subject = (a) => (a.subject_type === "group" ? `${t("sec.group")} ${a.subject_label}` : a.subject_label);

  return (
    <>
      <Card title={t("op.title")} flush>
        <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)", fontSize: "var(--fs-13)" }}>{t("op.global", { admins: byRole("admin"), observers: byRole("observateur") })}</p>
        {data.acl.length === 0 ? <p className="nx-muted" style={{ margin: "0 var(--space-4) var(--space-4)" }}>{t(kind === "vm" ? "op.noneVm" : "op.none")}</p> : (
          <TableWrap label={t("op.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.subject")}</th><th scope="col">{t("sec.role")}</th><th scope="col">{t("op.from")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.acl.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">{subject(a)}</th>
                    <td><Chip tone="accent">{data.roles[a.role]?.label || a.role}</Chip></td>
                    <td>{a.herite_de ? <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "permissions")}>{t("op.pool", { pool: a.herite_de })}</button> : <span className="nx-muted">{t("op.direct")}</span>}</td>
                    <td><div className="nx-ra">{!a.herite_de && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("op.removeX", { who: subject(a) })} onClick={() => remove(a)}>{t("sec.remove")}</button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <Card title={t("op.add")}>
        <div className="nx-fg">
          <Field label={t("sec.who")}>{(p) => <select {...p} className="nx-inp" value={f.subjectType} onChange={(e) => setF((x) => ({ ...x, subjectType: e.target.value, subjectId: "" }))}><option value="user">{t("sec.user")}</option><option value="group">{t("sec.group")}</option></select>}</Field>
          <Field label={t("sec.subject")} hint={f.subjectType === "user" ? t("op.usersHint") : null}>{(p) => <select {...p} className="nx-inp" value={f.subjectId} onChange={(e) => setF((x) => ({ ...x, subjectId: e.target.value }))}><option value="">{t("sec.choose")}</option>{subjects.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
          <Field label={t("sec.role")} hint={data.roles[role]?.description}>{(p) => <select {...p} className="nx-inp" value={role} onChange={(e) => setF((x) => ({ ...x, role: e.target.value }))}>{Object.entries(data.roles).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}</select>}</Field>
        </div>
        <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!f.subjectId || !role || busy} onClick={add}>{t("sec.assign")}</button></div>
      </Card>
    </>
  );
}

export function VmPermissionsPage({ resource }) { return <ObjectPermissions kind="vm" name={resource.nom} />; }
export function ContainerPermissionsPage({ resource }) { return <ObjectPermissions kind="container" name={resource.nom} />; }
