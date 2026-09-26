import { useCallback, useEffect, useState } from "react";
import {
  fetchUsers, createUser, updateUser, deleteUser,
  fetchGroups, createGroup, deleteGroup, addGroupMember, removeGroupMember,
  fetchPools, createPool, deletePool, addPoolMember, removePoolMember,
  fetchAclRoles, fetchAcl, createAcl, deleteAcl,
  fetchPrivileges, fetchCustomRoles, createCustomRole, deleteCustomRole, fetchContainers,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";

// Global roles stay `admin` / `observateur` (wire values); ACLs, groups, pools and custom roles only ADD
// scoped rights on top of them (app/core/permissions.py). Same endpoints and payloads as the historical tab.
const MIN_PASSWORD = 4;

export default function SecurityPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    try {
      const [users, groups, pools, roles, privileges, customRoles, acl, containers] = await Promise.all([
        fetchUsers(), fetchGroups(), fetchPools(), fetchAclRoles(), fetchPrivileges(), fetchCustomRoles(), fetchAcl(), fetchContainers().catch(() => []),
      ]);
      setData({ users, groups, pools, roles, privileges, customRoles, acl, containers });
      setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { reload(); }, [reload]);

  // Runs a mutation, then reloads; failures are shown with the backend's message.
  const run = useCallback(async (fn, { ok, fail }) => {
    try { await fn(); if (ok) pushToast({ kind: "success", title: ok.title, message: ok.message }); await reload(); return true; }
    catch (e) { pushToast({ kind: "error", title: fail, message: errorMessage(e) }); return false; }
  }, [pushToast, reload]);

  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return <p className="nx-muted" role="status">{t("loading")}</p>;
  const allRoles = { ...data.roles, ...Object.fromEntries(data.customRoles.map((r) => [r.key, r])) };
  const ctx = { t, run, data, vms, allRoles };

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="sec-global">
        <div className="nx-cardhead"><h2 id="sec-global">{t("sec.globalRoles")}</h2></div>
        <dl className="nx-dl">
          <dt className="nx-mono">admin</dt><dd>{t("sec.role.admin")}</dd>
          <dt className="nx-mono">observateur</dt><dd>{t("sec.role.observer")}</dd>
        </dl>
      </section>
      <UsersCard {...ctx} />
      <div className="nx-cols nx-cols--even">
        <GroupsCard {...ctx} />
        <PoolsCard {...ctx} />
      </div>
      <CustomRolesCard {...ctx} />
      <AclCard {...ctx} />
    </div>
  );
}

function UsersCard({ t, run, data }) {
  const me = useAuthStore((s) => s.username);
  const [f, setF] = useState({ username: "", password: "", role: "observateur" });
  const valid = f.username.trim() && f.password.length >= MIN_PASSWORD;
  async function create(e) {
    e.preventDefault();
    if (await run(() => createUser(f.username.trim(), f.password, f.role), { ok: { title: t("sec.userCreated"), message: f.username.trim() }, fail: t("sec.createFailed") })) setF({ username: "", password: "", role: "observateur" });
  }
  async function remove(u) {
    if (!(await confirmAction({ title: t("sec.userDeleteTitle", { name: u.username }), message: t("sec.userDeleteMsg"), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    run(() => deleteUser(u.username), { ok: { title: t("sec.userDeleted"), message: u.username }, fail: t("sec.deleteFailed") });
  }
  async function changeRole(u, role) {
    if (role === "admin" && !(await confirmAction({ title: t("sec.promoteTitle", { name: u.username }), message: t("sec.promoteMsg"), confirmLabel: t("sec.promote"), danger: true }))) return;
    run(() => updateUser(u.username, { role }), { fail: t("sec.updateFailed") });
  }
  return (
    <section className="nx-card" aria-labelledby="sec-users">
      <div className="nx-cardhead"><h2 id="sec-users">{t("sec.users")} <span className="nx-count">{data.users.length}</span></h2></div>
      <form className="nx-form nx-form--inline" onSubmit={create}>
        <div className="nx-formgrid">
          <label>{t("sec.username")}<input className="nx-input" aria-label="Username" value={f.username} autoComplete="off" onChange={(e) => setF({ ...f, username: e.target.value })} /></label>
          <label>{t("ct.password")}<input className="nx-input" aria-label="Password" type="password" value={f.password} autoComplete="new-password" onChange={(e) => setF({ ...f, password: e.target.value })} /><span className="nx-hint">{t("sec.passwordHelp", { n: MIN_PASSWORD })}</span></label>
          <label>{t("sec.role")}<select className="nx-input" aria-label="Role of the new user" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="observateur">{t("sec.observer")}</option><option value="admin">{t("sec.admin")}</option></select></label>
        </div>
        <div className="nx-formactions"><button type="submit" className="nx-btn nx-btn--primary" disabled={!valid}>{t("action.create")}</button></div>
      </form>
      <div className="nx-tablewrap">
        <table className="nx-table">
          <thead><tr><th scope="col">{t("sec.username")}</th><th scope="col">{t("sec.role")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
          <tbody>
            {data.users.map((u) => {
              const self = u.username === me;
              return (
                <tr key={u.username}>
                  <th scope="row" className="nx-mono">{u.username}{self && <span className="nx-muted"> ({t("sec.you")})</span>}</th>
                  <td><select className="nx-input nx-input--auto" aria-label={`Role of ${u.username}`} value={u.role} disabled={self} title={self ? t("sec.selfRole") : undefined} onChange={(e) => changeRole(u, e.target.value)}><option value="observateur">{t("sec.observer")}</option><option value="admin">{t("sec.admin")}</option></select></td>
                  <td className="nx-num"><button type="button" className="nx-btn nx-btn--danger" disabled={self} title={self ? t("sec.selfDelete") : undefined} aria-label={self ? t("sec.selfDelete") : `Delete user ${u.username}`} onClick={() => remove(u)}>{t("menu.delete").replace("…", "")}</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// Group / pool cards share one shape: a name, removable chips, an add control.
function MemberCard({ id, title, help, items, empty, name, setName, create, createLabel, nameLabel, cards }) {
  return (
    <section className="nx-card" aria-labelledby={id}>
      <div className="nx-cardhead"><h2 id={id}>{title} <span className="nx-count">{items ? items.length : "…"}</span></h2></div>
      {help && <p className="nx-muted" style={{ marginTop: 0 }}>{help}</p>}
      <form className="nx-form nx-form--inline" onSubmit={(e) => { e.preventDefault(); create(); }}>
        <div className="nx-inline">
          <input className="nx-input" aria-label={nameLabel} placeholder={nameLabel} value={name} onChange={(e) => setName(e.target.value)} />
          <button type="submit" className="nx-btn nx-btn--primary" disabled={!name.trim()}>{createLabel}</button>
        </div>
      </form>
      {items.length === 0 ? <p className="nx-muted" role="status">{empty}</p> : <div className="nx-stack">{cards}</div>}
    </section>
  );
}

function Chips({ t, list, onRemove, label, none }) {
  return (
    <ul className="nx-tags" aria-label={label}>
      {list.length === 0 && <li className="nx-muted">{none}</li>}
      {list.map((m) => <li key={m} className="nx-tag"><span className="nx-mono">{m}</span><button type="button" aria-label={`${t("sec.remove")} ${m}`} onClick={() => onRemove(m)}>×</button></li>)}
    </ul>
  );
}

function GroupsCard({ t, run, data }) {
  const [name, setName] = useState("");
  const [member, setMember] = useState({});
  const del = t("menu.delete").replace("…", "");
  const removeGroup = async (g) => { if (await confirmAction({ title: t("sec.groupDeleteTitle", { name: g.name }), message: t("sec.groupDeleteMsg"), confirmLabel: del, danger: true })) run(() => deleteGroup(g.id), { ok: { title: t("sec.groupDeleted"), message: g.name }, fail: t("sec.deleteFailed") }); };
  const removeMember = async (g, m) => { if (await confirmAction({ title: t("sec.memberRemoveTitle", { name: m }), message: t("sec.memberRemoveMsg"), confirmLabel: t("sec.remove") })) run(() => removeGroupMember(g.id, m), { fail: t("sec.removeFailed") }); };
  const addMember = async (g) => { const u = (member[g.id] || "").trim(); if (u && (await run(() => addGroupMember(g.id, u), { fail: t("sec.addFailed") }))) setMember((s) => ({ ...s, [g.id]: "" })); };
  return (
    <MemberCard id="sec-groups" title={t("sec.groups")} items={data.groups} empty={t("sec.noGroups")} name={name} setName={setName} nameLabel={t("sec.groupName")} createLabel={t("action.create")}
      create={async () => { if (await run(() => createGroup(name.trim()), { ok: { title: t("sec.groupCreated"), message: name.trim() }, fail: t("sec.createFailed") })) setName(""); }}
      cards={data.groups.map((g) => (
        <div key={g.id} className="nx-subcard">
          <div className="nx-cardhead"><h3>{g.name}</h3><button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete group ${g.name}`} onClick={() => removeGroup(g)}>{del}</button></div>
          <Chips t={t} list={g.membres} label={`${t("sec.members")} ${g.name}`} none={t("sec.noMembers")} onRemove={(m) => removeMember(g, m)} />
          <div className="nx-inline">
            <select className="nx-input" aria-label={`${t("sec.addMember")} ${g.name}`} value={member[g.id] || ""} onChange={(e) => setMember((s) => ({ ...s, [g.id]: e.target.value }))}>
              <option value="">{t("sec.chooseUser")}</option>
              {data.users.filter((u) => !g.membres.includes(u.username)).map((u) => <option key={u.username} value={u.username}>{u.username}</option>)}
            </select>
            <button type="button" className="nx-btn" disabled={!member[g.id]} onClick={() => addMember(g)}>{t("sec.add")}</button>
          </div>
        </div>
      ))} />
  );
}

function PoolsCard({ t, run, data, vms }) {
  const [name, setName] = useState("");
  const [pick, setPick] = useState({});
  const del = t("menu.delete").replace("…", "");
  const removePool = async (p) => { if (await confirmAction({ title: t("sec.poolDeleteTitle", { name: p.name }), message: t("sec.poolDeleteMsg"), confirmLabel: del, danger: true })) run(() => deletePool(p.id), { ok: { title: t("sec.poolDeleted"), message: p.name }, fail: t("sec.deleteFailed") }); };
  const removeVm = async (p, v) => { if (await confirmAction({ title: t("sec.vmRemoveTitle", { name: v }), message: t("sec.vmRemoveMsg"), confirmLabel: t("sec.remove") })) run(() => removePoolMember(p.id, v), { fail: t("sec.removeFailed") }); };
  return (
    <MemberCard id="sec-pools" title={t("sec.pools")} help={t("sec.poolsHelp")} items={data.pools} empty={t("sec.noPools")} name={name} setName={setName} nameLabel={t("sec.poolName")} createLabel={t("action.create")}
      create={async () => { if (await run(() => createPool(name.trim()), { ok: { title: t("sec.poolCreated"), message: name.trim() }, fail: t("sec.createFailed") })) setName(""); }}
      cards={data.pools.map((p) => {
        const available = vms.filter((v) => !p.vms.includes(v.nom));
        return (
          <div key={p.id} className="nx-subcard">
            <div className="nx-cardhead"><h3>{p.name}</h3><button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete pool ${p.name}`} onClick={() => removePool(p)}>{del}</button></div>
            <Chips t={t} list={p.vms} label={`VM ${p.name}`} none={t("sec.noVms")} onRemove={(v) => removeVm(p, v)} />
            {available.length > 0 && (
              <div className="nx-inline">
                <select className="nx-input" aria-label={`${t("sec.addVm")} ${p.name}`} value={pick[p.id] || ""} onChange={(e) => setPick((s) => ({ ...s, [p.id]: e.target.value }))}>
                  <option value="">{t("sec.chooseVm")}</option>
                  {available.map((v) => <option key={v.nom} value={v.nom}>{v.nom}</option>)}
                </select>
                <button type="button" className="nx-btn" disabled={!pick[p.id]} onClick={() => run(() => addPoolMember(p.id, pick[p.id]), { fail: t("sec.addFailed") })}>{t("sec.add")}</button>
              </div>
            )}
          </div>
        );
      })} />
  );
}

function CustomRolesCard({ t, run, data }) {
  const [name, setName] = useState("");
  const [sel, setSel] = useState({});
  const chosen = Object.keys(sel).filter((k) => sel[k]);
  const del = t("menu.delete").replace("…", "");
  async function create(e) {
    e.preventDefault();
    if (await run(() => createCustomRole(name.trim(), chosen), { ok: { title: t("sec.roleCreated"), message: name.trim() }, fail: t("sec.createFailed") })) { setName(""); setSel({}); }
  }
  async function remove(r) {
    if (await confirmAction({ title: t("sec.roleDeleteTitle", { name: r.label }), message: t("sec.roleDeleteMsg"), confirmLabel: del, danger: true })) run(() => deleteCustomRole(r.id), { ok: { title: t("sec.roleDeleted"), message: r.label }, fail: t("sec.deleteFailed") });
  }
  return (
    <section className="nx-card" aria-labelledby="sec-roles">
      <div className="nx-cardhead"><h2 id="sec-roles">{t("sec.customRoles")} <span className="nx-count">{data.customRoles.length}</span></h2></div>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("sec.customRolesHelp")}</p>
      <form className="nx-form" onSubmit={create}>
        <label>{t("sec.roleName")}<input className="nx-input" aria-label="Role name" placeholder="backups-only" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <fieldset className="nx-fieldset">
          <legend>{t("sec.privileges")}</legend>
          <div className="nx-checks">{Object.entries(data.privileges).map(([k, label]) => <label key={k} className="nx-check"><input type="checkbox" checked={!!sel[k]} onChange={() => setSel((s) => ({ ...s, [k]: !s[k] }))} /> {label}</label>)}</div>
        </fieldset>
        <div className="nx-formactions"><button type="submit" className="nx-btn nx-btn--primary" disabled={!name.trim() || chosen.length === 0}>{t("sec.createRole", { n: chosen.length })}</button></div>
      </form>
      {data.customRoles.length === 0 ? <p className="nx-muted" role="status">{t("sec.noRoles")}</p> : (
        <ul className="nx-list nx-list--vols">
          {data.customRoles.map((r) => <li key={r.key}><strong>{r.label}</strong><span className="nx-muted">{[...r.privileges].map((p) => data.privileges[p] || p).join(", ")}</span><button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete role ${r.label}`} onClick={() => remove(r)}>{del}</button></li>)}
        </ul>
      )}
    </section>
  );
}

function AclCard({ t, run, data, vms, allRoles }) {
  const [f, setF] = useState({ subjectType: "user", subjectId: "", role: Object.keys(allRoles)[0] || "", resourceType: "vm", resourceId: "" });
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value, ...(k === "subjectType" ? { subjectId: "" } : {}), ...(k === "resourceType" ? { resourceId: "" } : {}) }));
  const role = allRoles[f.role] ? f.role : Object.keys(allRoles)[0];
  const resources = f.resourceType === "vm" ? vms.map((v) => [v.nom, v.nom]) : f.resourceType === "pool" ? data.pools.map((p) => [String(p.id), p.name]) : data.containers.map((c) => [c.nom, c.nom]);
  async function create(e) {
    e.preventDefault();
    if (await run(() => createAcl({ subject_type: f.subjectType, subject_id: f.subjectId, role, resource_type: f.resourceType, resource_id: f.resourceId }), { ok: { title: t("sec.assigned") }, fail: t("sec.assignFailed") })) setF((x) => ({ ...x, subjectId: "", resourceId: "" }));
  }
  async function remove(a) {
    if (await confirmAction({ title: t("sec.aclDeleteTitle"), message: t("sec.aclDeleteMsg"), confirmLabel: t("sec.remove"), danger: true })) run(() => deleteAcl(a.id), { fail: t("sec.removeFailed") });
  }
  const resLabel = (a) => (a.resource_type === "pool" ? `${t("sec.pool")} ${a.resource_label}` : a.resource_type === "container" ? `${t("sec.container")} ${a.resource_label}` : a.resource_label);
  return (
    <section className="nx-card" aria-labelledby="sec-acl">
      <div className="nx-cardhead"><h2 id="sec-acl">{t("sec.acl")} <span className="nx-count">{data.acl.length}</span></h2></div>
      <form className="nx-form" onSubmit={create}>
        <div className="nx-formgrid">
          <label>{t("sec.who")}<select className="nx-input" aria-label="Who" value={f.subjectType} onChange={set("subjectType")}><option value="user">{t("sec.user")}</option><option value="group">{t("sec.group")}</option></select></label>
          <label>{t("sec.subject")}<select className="nx-input" aria-label="Subject" value={f.subjectId} onChange={set("subjectId")}><option value="">{t("sec.choose")}</option>{f.subjectType === "user" ? data.users.map((u) => <option key={u.username} value={u.username}>{u.username}</option>) : data.groups.map((g) => <option key={g.id} value={String(g.id)}>{g.name}</option>)}</select></label>
          <label>{t("sec.role")}<select className="nx-input" aria-label="Role" value={role} onChange={set("role")}>{Object.entries(allRoles).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}</select></label>
          <label>{t("sec.on")}<select className="nx-input" aria-label="On" value={f.resourceType} onChange={set("resourceType")}><option value="vm">{t("sec.aVm")}</option><option value="pool">{t("sec.aPool")}</option><option value="container">{t("sec.aContainer")}</option></select></label>
          <label>{t("sec.resource")}<select className="nx-input" aria-label="Resource" value={f.resourceId} onChange={set("resourceId")}><option value="">{t("sec.choose")}</option>{resources.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        </div>
        {allRoles[role]?.description && <span className="nx-hint">{allRoles[role].description}</span>}
        <div className="nx-formactions"><button type="submit" className="nx-btn nx-btn--primary" disabled={!f.subjectId || !f.resourceId}>{t("sec.assign")}</button></div>
      </form>
      {data.acl.length === 0 ? <p className="nx-muted" role="status">{t("sec.noAcl")}</p> : (
        <div className="nx-tablewrap">
          <table className="nx-table">
            <thead><tr><th scope="col">{t("sec.who")}</th><th scope="col">{t("sec.role")}</th><th scope="col">{t("sec.on")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {data.acl.map((a) => (
                <tr key={a.id}>
                  <th scope="row">{a.subject_type === "group" ? `${t("sec.group")} ${a.subject_label}` : a.subject_label}</th>
                  <td>{allRoles[a.role]?.label || a.role}</td>
                  <td className="nx-mono">{resLabel(a)}</td>
                  <td className="nx-num"><button type="button" className="nx-btn nx-btn--danger" aria-label={`Remove assignment ${a.id}`} onClick={() => remove(a)}>{t("sec.remove")}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
