import LoadingState from "../../components/LoadingState";
import { confirmAction } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Users, Boxes, ShieldCheck, UserPlus, X } from "lucide-react";
import {
  fetchUsers, createUser, updateUser, deleteUser,
  fetchGroups, createGroup, deleteGroup, addGroupMember, removeGroupMember,
  fetchPools, createPool, deletePool, addPoolMember, removePoolMember,
  fetchAclRoles, fetchAcl, createAcl, deleteAcl,
  fetchPrivileges, fetchCustomRoles, createCustomRole, deleteCustomRole,
  fetchContainers,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { NativeSelect } from "@/components/ui/native-select";

// Global roles (app/core/database.py: role IN ('admin','observateur')) are
// unchanged by this system, which only ADDS scoped rights on top of them (see
// app/core/permissions.py).
const GLOBAL_ROLES = [
  { nom: "admin", description: "Full access: VM creation/deletion, destructive actions, SSH terminal, console." },
  { nom: "observateur", description: "Global read-only access (and VNC console) to everything. No modifying action without an explicit assignment below." },
];

export default function PermissionsTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const [users, setUsers] = useState(null);
  const [groups, setGroups] = useState(null);
  const [pools, setPools] = useState(null);
  const [roles, setRoles] = useState(null);
  const [privileges, setPrivileges] = useState(null);
  const [customRoles, setCustomRoles] = useState(null);
  const [acl, setAcl] = useState(null);
  const [containers, setContainers] = useState(null);

  const reloadAll = useCallback(async () => {
    try {
      const [u, g, p, r, pv, cr, a, ct] = await Promise.all([
        fetchUsers(), fetchGroups(), fetchPools(), fetchAclRoles(), fetchPrivileges(), fetchCustomRoles(), fetchAcl(),
        fetchContainers(),
      ]);
      setUsers(u); setGroups(g); setPools(p); setRoles(r); setPrivileges(pv); setCustomRoles(cr); setAcl(a); setContainers(ct);
    } catch (e) {
      pushToast({ kind: "error", title: "Permissions error", message: e.message });
    }
  }, [pushToast]);

  // Merge predefined + custom roles into a single table for the assignment selector
  // (AclSection): the same "custom:<id>" keys as on the backend, transparent for the
  // user.
  const allRoles = roles && customRoles
    ? { ...roles, ...Object.fromEntries(customRoles.map((r) => [r.key, r])) }
    : null;

  useEffect(() => { reloadAll(); }, [reloadAll]);

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h3 className="text-sm font-semibold text-foreground mb-2">Global roles</h3>
        <div className="divide-y divide-border">
          {GLOBAL_ROLES.map((r) => (
            <div key={r.nom} className="py-2">
              <div className="text-sm font-medium text-foreground">{r.nom}</div>
              <div className="text-xs text-muted-foreground">{r.description}</div>
            </div>
          ))}
        </div>
      </Card>

      <UsersSection users={users} reload={reloadAll} pushToast={pushToast} />

      <GroupsSection groups={groups} reload={reloadAll} pushToast={pushToast} />
      <PoolsSection pools={pools} vms={vms} reload={reloadAll} pushToast={pushToast} />
      <CustomRolesSection customRoles={customRoles} privileges={privileges} reload={reloadAll} pushToast={pushToast} />
      <AclSection acl={acl} roles={allRoles} groups={groups} pools={pools} vms={vms} containers={containers} users={users} reload={reloadAll} pushToast={pushToast} />
    </div>
  );
}

function CustomRolesSection({ customRoles, privileges, reload, pushToast }) {
  const [name, setName] = useState("");
  const [selected, setSelected] = useState({});
  const [busy, setBusy] = useState(false);

  function toggle(key) {
    setSelected((s) => ({ ...s, [key]: !s[key] }));
  }

  async function handleCreate() {
    const privs = Object.entries(selected).filter(([, v]) => v).map(([k]) => k);
    if (!name.trim() || privs.length === 0) return;
    setBusy(true);
    try {
      await createCustomRole(name.trim(), privs);
      pushToast({ kind: "success", title: "Role created", message: name.trim() });
      setName(""); setSelected({});
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id, roleName) {
    if (!(await confirmAction({ title: `Delete role '${roleName}'?`, message: "Assignments that use this role stop granting access.", confirmLabel: "Delete" }))) return;
    setBusy(true);
    try {
      await deleteCustomRole(id);
      pushToast({ kind: "success", title: "Role deleted", message: roleName });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  const ready = customRoles && privileges;
  const selectedCount = Object.values(selected).filter(Boolean).length;

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck size={15} className="text-foreground/80" />
        <h3 className="text-sm font-semibold text-foreground">Custom roles</h3>
      </div>
      <p className="text-xs text-muted-foreground mb-3">Build a role by picking exactly the allowed actions, in addition to Reader/Operator/Manager.</p>

      {!ready ? (
        <p className="text-sm text-muted-foreground"><LoadingState /></p>
      ) : (
        <>
          <div className="rounded-md border border-border p-3 mb-3">
            <Input aria-label="Role name (e.g. backups-only)" className="mb-2" placeholder="Role name (e.g. backups-only)" value={name} onChange={(e) => setName(e.target.value)} />
            <div className="grid grid-cols-1 gap-1.5 mb-2 sm:grid-cols-2">
              {Object.entries(privileges).map(([key, label]) => (
                <Label key={key} className="flex items-center gap-2 text-xs text-foreground/90 cursor-pointer font-normal">
                  <Checkbox checked={!!selected[key]} onCheckedChange={() => toggle(key)} />
                  {label}
                </Label>
              ))}
            </div>
            <Button disabled={busy || !name.trim() || selectedCount === 0} onClick={handleCreate}>
              <Plus /> Create ({selectedCount} privilege{selectedCount > 1 ? "s" : ""})
            </Button>
          </div>

          {customRoles.length === 0 ? (
            <p className="text-sm text-muted-foreground">No custom roles.</p>
          ) : (
            <div className="divide-y divide-border">
              {customRoles.map((r) => (
                <div key={r.key} className="flex items-center justify-between py-2 text-sm">
                  <div>
                    <span className="text-foreground font-medium">{r.label}</span>
                    <span className="text-muted-foreground"> -- {[...r.privileges].map((p) => privileges[p] || p).join(", ")}</span>
                  </div>
                  <Button aria-label={`Delete role ${r.label}`} variant="ghost" size="icon" className="size-7 text-muted-foreground hover:text-status-error" disabled={busy} onClick={() => handleDelete(r.id, r.label)}>
                    <Trash2 size={14} />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function UsersSection({ users, reload, pushToast }) {
  const me = useAuthStore((s) => s.username);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState("observateur");
  const [busy, setBusy] = useState(false);

  async function handleCreate() {
    if (!newUsername.trim() || newPassword.length < 4) return;
    setBusy(true);
    try {
      await createUser(newUsername.trim(), newPassword, newRole);
      pushToast({ kind: "success", title: "User created", message: newUsername.trim() });
      setNewUsername(""); setNewPassword(""); setNewRole("observateur");
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleRoleChange(username, role) {
    setBusy(true);
    try {
      await updateUser(username, { role });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(username) {
    if (!(await confirmAction({ title: "Please confirm", message: `Delete user '${username}'?`, confirmLabel: "Confirm" }))) return;
    setBusy(true);
    try {
      await deleteUser(username);
      pushToast({ kind: "success", title: "User deleted", message: username });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <UserPlus size={15} className="text-foreground/80" />
        <h3 className="text-sm font-semibold text-foreground">Users</h3>
      </div>

      <div className="grid grid-cols-1 gap-2 mb-3 sm:grid-cols-4">
        <Input aria-label="Username" placeholder="Username" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} />
        <Input aria-label="Password (min. 4)" type="password" placeholder="Password (min. 4)" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        <NativeSelect aria-label="Role of the new user" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
          <option value="observateur">observer</option>
          <option value="admin">admin</option>
        </NativeSelect>
        <Button disabled={busy || !newUsername.trim() || newPassword.length < 4} onClick={handleCreate}>
          <Plus /> Create
        </Button>
      </div>

      {users == null ? (
        <p className="text-sm text-muted-foreground"><LoadingState /></p>
      ) : (
        <div className="divide-y divide-border">
          {users.map((u) => (
            <div key={u.username} className="flex items-center justify-between py-2 text-sm">
              <span className="text-foreground">{u.username}{u.username === me && <span className="text-muted-foreground"> (you)</span>}</span>
              <div className="flex items-center gap-2">
                <NativeSelect
                  aria-label={`Role of ${u.username}`}
                  className="w-auto text-xs"
                  value={u.role}
                  disabled={busy || u.username === me}
                  onChange={(e) => handleRoleChange(u.username, e.target.value)}
                >
                  <option value="observateur">observer</option>
                  <option value="admin">admin</option>
                </NativeSelect>
                <Button aria-label={u.username === me ? "You cannot delete yourself" : `Delete user ${u.username}`}
                  variant="ghost" size="icon" className="size-7 text-muted-foreground hover:text-status-error disabled:opacity-30"
                  disabled={busy || u.username === me}
                  title={u.username === me ? "You cannot delete yourself" : "Delete"}
                  onClick={() => handleDelete(u.username)}
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function GroupsSection({ groups, reload, pushToast }) {
  const [newName, setNewName] = useState("");
  const [memberInputs, setMemberInputs] = useState({});
  const [busy, setBusy] = useState(false);

  async function handleCreate() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await createGroup(newName.trim());
      setNewName("");
      pushToast({ kind: "success", title: "Group created", message: newName.trim() });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id, name) {
    if (!(await confirmAction({ title: `Delete group '${name}'?`, message: "Rights granted to this group are lost for its members.", confirmLabel: "Delete" }))) return;
    setBusy(true);
    try {
      await deleteGroup(id);
      pushToast({ kind: "success", title: "Group deleted", message: name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleAddMember(id) {
    const username = (memberInputs[id] || "").trim();
    if (!username) return;
    setBusy(true);
    try {
      await addGroupMember(id, username);
      setMemberInputs((s) => ({ ...s, [id]: "" }));
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Add failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleRemoveMember(id, username) {
    if (!(await confirmAction({ title: `Remove '${username}' from the group?`, message: "The user loses the rights granted through this group.", confirmLabel: "Remove" }))) return;
    setBusy(true);
    try {
      await removeGroupMember(id, username);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Remove failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <Users size={15} className="text-foreground/80" />
        <h3 className="text-sm font-semibold text-foreground">User groups</h3>
      </div>

      <div className="flex gap-2 mb-3">
        <Input aria-label="Group name (e.g. devs)" placeholder="Group name (e.g. devs)" value={newName} onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()} />
        <Button className="shrink-0" disabled={busy || !newName.trim()} onClick={handleCreate}>
          <Plus /> Create
        </Button>
      </div>

      {groups == null ? (
        <p className="text-sm text-muted-foreground"><LoadingState /></p>
      ) : groups.length === 0 ? (
        <p className="text-sm text-muted-foreground">No groups. Create a group to assign rights to several users at once.</p>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => (
            <div key={g.id} className="rounded-md border border-border p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-foreground">{g.name}</span>
                <Button aria-label={`Delete group ${g.name}`} variant="ghost" size="icon" className="size-7 text-muted-foreground hover:text-status-error" disabled={busy} onClick={() => handleDelete(g.id, g.name)}>
                  <Trash2 size={14} />
                </Button>
              </div>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {g.membres.length === 0 && <span className="text-xs text-muted-foreground">No members</span>}
                {g.membres.map((m) => (
                  <Badge key={m} variant="secondary" className="gap-1">
                    {m}
                    <button className="text-muted-foreground hover:text-status-error" onClick={() => handleRemoveMember(g.id, m)}><X size={11} /></button>
                  </Badge>
                ))}
              </div>
              <div className="flex gap-1.5">
                <Input aria-label="username" className="text-xs" placeholder="username" value={memberInputs[g.id] || ""}
                  onChange={(e) => setMemberInputs((s) => ({ ...s, [g.id]: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && handleAddMember(g.id)} />
                <Button variant="secondary" size="sm" className="shrink-0" disabled={busy} onClick={() => handleAddMember(g.id)}>Add</Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function PoolsSection({ pools, vms, reload, pushToast }) {
  const [newName, setNewName] = useState("");
  const [vmSelect, setVmSelect] = useState({});
  const [busy, setBusy] = useState(false);

  async function handleCreate() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await createPool(newName.trim());
      setNewName("");
      pushToast({ kind: "success", title: "Pool created", message: newName.trim() });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id, name) {
    if (!(await confirmAction({ title: `Delete pool '${name}'?`, message: "Rights granted on this pool are lost. The VMs themselves are not touched.", confirmLabel: "Delete" }))) return;
    setBusy(true);
    try {
      await deletePool(id);
      pushToast({ kind: "success", title: "Pool deleted", message: name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleAddVm(id) {
    const vmName = vmSelect[id];
    if (!vmName) return;
    setBusy(true);
    try {
      await addPoolMember(id, vmName);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Add failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleRemoveVm(id, vmName) {
    if (!(await confirmAction({ title: `Remove '${vmName}' from the pool?`, message: "Rights granted through this pool no longer apply to this VM.", confirmLabel: "Remove" }))) return;
    setBusy(true);
    try {
      await removePoolMember(id, vmName);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Remove failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <Boxes size={15} className="text-foreground/80" />
        <h3 className="text-sm font-semibold text-foreground">VM pools</h3>
      </div>
      <p className="text-xs text-muted-foreground mb-3">Group VMs (e.g. "Project-A") to assign them rights in one go, without listing them one by one.</p>

      <div className="flex gap-2 mb-3">
        <Input aria-label="Pool name (e.g. project-a)" placeholder="Pool name (e.g. project-a)" value={newName} onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()} />
        <Button className="shrink-0" disabled={busy || !newName.trim()} onClick={handleCreate}>
          <Plus /> Create
        </Button>
      </div>

      {pools == null ? (
        <p className="text-sm text-muted-foreground"><LoadingState /></p>
      ) : pools.length === 0 ? (
        <p className="text-sm text-muted-foreground">No pools.</p>
      ) : (
        <div className="space-y-3">
          {pools.map((p) => {
            const available = vms.filter((v) => !p.vms.includes(v.nom));
            return (
              <div key={p.id} className="rounded-md border border-border p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-foreground">{p.name}</span>
                  <Button aria-label={`Delete pool ${p.name}`} variant="ghost" size="icon" className="size-7 text-muted-foreground hover:text-status-error" disabled={busy} onClick={() => handleDelete(p.id, p.name)}>
                    <Trash2 size={14} />
                  </Button>
                </div>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {p.vms.length === 0 && <span className="text-xs text-muted-foreground">No VMs</span>}
                  {p.vms.map((v) => (
                    <Badge key={v} variant="secondary" className="gap-1">
                      {v}
                      <button className="text-muted-foreground hover:text-status-error" onClick={() => handleRemoveVm(p.id, v)}><X size={11} /></button>
                    </Badge>
                  ))}
                </div>
                {available.length > 0 && (
                  <div className="flex gap-1.5">
                    <NativeSelect
                      aria-label={`VM to add to pool ${p.name}`}
                      className="text-xs"
                      value={vmSelect[p.id] || ""}
                      onChange={(e) => setVmSelect((s) => ({ ...s, [p.id]: e.target.value }))}
                    >
                      <option value="">Choose a VM...</option>
                      {available.map((v) => <option key={v.nom} value={v.nom}>{v.nom}</option>)}
                    </NativeSelect>
                    <Button variant="secondary" size="sm" className="shrink-0" disabled={busy || !vmSelect[p.id]} onClick={() => handleAddVm(p.id)}>Add</Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function AclSection({ acl, roles, groups, pools, vms, containers, users, reload, pushToast }) {
  const [subjectType, setSubjectType] = useState("user");
  const [subjectId, setSubjectId] = useState("");
  const [role, setRole] = useState("");
  const [resourceType, setResourceType] = useState("vm");
  const [resourceId, setResourceId] = useState("");
  const [busy, setBusy] = useState(false);

  const ready = roles && groups && pools && users;
  useEffect(() => {
    if (!role && roles) setRole(Object.keys(roles)[0] || "");
  }, [roles, role]);

  async function handleCreate() {
    if (!subjectId || !role || !resourceId) return;
    setBusy(true);
    try {
      await createAcl({ subject_type: subjectType, subject_id: subjectId, role, resource_type: resourceType, resource_id: resourceId });
      pushToast({ kind: "success", title: "Assignment created" });
      setSubjectId(""); setResourceId("");
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Assignment failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id) {
    if (!(await confirmAction({ title: "Remove this assignment?", message: "The subject loses the access granted by this assignment.", confirmLabel: "Remove" }))) return;
    setBusy(true);
    try {
      await deleteAcl(id);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <ShieldCheck size={15} className="text-foreground/80" />
        <h3 className="text-sm font-semibold text-foreground">Assignments (who has which role, on what)</h3>
      </div>

      {!ready ? (
        <p className="text-sm text-muted-foreground"><LoadingState /></p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 mb-2 sm:grid-cols-4">
            <div>
              <Label className="text-[11px] text-muted-foreground mb-1 block">Who</Label>
              <NativeSelect aria-label="Who" className="text-xs w-full" value={subjectType} onChange={(e) => { setSubjectType(e.target.value); setSubjectId(""); }}>
                <option value="user">User</option>
                <option value="group">Group</option>
              </NativeSelect>
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground mb-1 block">&nbsp;</Label>
              <NativeSelect aria-label="Subject" className="text-xs w-full" value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
                <option value="">Choose...</option>
                {subjectType === "user"
                  ? users.map((u) => <option key={u.username} value={u.username}>{u.username}</option>)
                  : groups.map((g) => <option key={g.id} value={String(g.id)}>{g.name}</option>)}
              </NativeSelect>
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground mb-1 block">Role</Label>
              <NativeSelect aria-label="Role" className="text-xs w-full" value={role} onChange={(e) => setRole(e.target.value)}>
                {Object.entries(roles).map(([key, r]) => <option key={key} value={key}>{r.label}</option>)}
              </NativeSelect>
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground mb-1 block">On</Label>
              <NativeSelect aria-label="On" className="text-xs w-full" value={resourceType} onChange={(e) => { setResourceType(e.target.value); setResourceId(""); }}>
                <option value="vm">A VM</option>
                <option value="pool">A pool</option>
                <option value="container">A container</option>
              </NativeSelect>
            </div>
          </div>
          <div className="flex gap-2 mb-3">
            <NativeSelect aria-label="Resource" className="flex-1" value={resourceId} onChange={(e) => setResourceId(e.target.value)}>
              <option value="">{resourceType === "vm" ? "Choose a VM..." : resourceType === "pool" ? "Choose a pool..." : "Choose a container..."}</option>
              {resourceType === "vm"
                ? vms.map((v) => <option key={v.nom} value={v.nom}>{v.nom}</option>)
                : resourceType === "pool"
                ? pools.map((p) => <option key={p.id} value={String(p.id)}>{p.name}</option>)
                : (containers || []).map((c) => <option key={c.nom} value={c.nom}>{c.nom}</option>)}
            </NativeSelect>
            <Button className="shrink-0" disabled={busy || !subjectId || !resourceId} onClick={handleCreate}>
              <Plus /> Assign
            </Button>
          </div>

          {roles[role] && (
            <p className="text-[11px] text-muted-foreground mb-3">{roles[role].description}</p>
          )}

          {acl && acl.length > 0 ? (
            <div className="divide-y divide-border">
              {acl.map((a) => (
                <div key={a.id} className="flex items-center justify-between py-2 text-sm">
                  <div className="text-foreground">
                    <span className="font-medium">{a.subject_type === "group" ? `Group ${a.subject_label}` : a.subject_label}</span>
                    <span className="text-muted-foreground"> -- {roles[a.role]?.label || a.role} -- </span>
                    <span>
                      {a.resource_type === "pool" ? `Pool ${a.resource_label}` : a.resource_type === "container" ? `Container ${a.resource_label}` : a.resource_label}
                    </span>
                  </div>
                  <Button aria-label="Remove assignment" variant="ghost" size="icon" className="size-7 text-muted-foreground hover:text-status-error" disabled={busy} onClick={() => handleDelete(a.id)}>
                    <Trash2 size={14} />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No assignments: access stays limited to the global roles above.</p>
          )}
        </>
      )}
    </Card>
  );
}
