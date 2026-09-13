import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Users, Boxes, ShieldCheck, UserPlus } from "lucide-react";
import {
  fetchUsers, createUser, updateUser, deleteUser,
  fetchGroups, createGroup, deleteGroup, addGroupMember, removeGroupMember,
  fetchPools, createPool, deletePool, addPoolMember, removePoolMember,
  fetchAclRoles, fetchAcl, createAcl, deleteAcl,
  fetchPrivileges, fetchCustomRoles, createCustomRole, deleteCustomRole,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";

// Roles globaux (app/core/database.py : role IN ('admin','observateur')) --
// inchanges par ce systeme, qui se contente d'AJOUTER des droits scopes
// par-dessus (voir app/core/permissions.py).
const GLOBAL_ROLES = [
  { nom: "admin", description: "Accès complet : création/suppression de VM, actions destructives, terminal SSH, console." },
  { nom: "observateur", description: "Lecture seule globale (et console VNC) sur tout. Aucune action de modification sans attribution explicite ci-dessous." },
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

  const reloadAll = useCallback(async () => {
    try {
      const [u, g, p, r, pv, cr, a] = await Promise.all([
        fetchUsers(), fetchGroups(), fetchPools(), fetchAclRoles(), fetchPrivileges(), fetchCustomRoles(), fetchAcl(),
      ]);
      setUsers(u); setGroups(g); setPools(p); setRoles(r); setPrivileges(pv); setCustomRoles(cr); setAcl(a);
    } catch (e) {
      pushToast({ kind: "error", title: "Erreur permissions", message: e.message });
    }
  }, [pushToast]);

  // Fusion roles predefinis + personnalises en une seule table, pour le
  // selecteur d'attribution (AclSection) : memes clefs "custom:<id>" que
  // cote backend, transparent pour l'utilisateur.
  const allRoles = roles && customRoles
    ? { ...roles, ...Object.fromEntries(customRoles.map((r) => [r.key, r])) }
    : null;

  useEffect(() => { reloadAll(); }, [reloadAll]);

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-anthracite-100 mb-2">Rôles globaux</h3>
        <div className="divide-y divide-anthracite-600">
          {GLOBAL_ROLES.map((r) => (
            <div key={r.nom} className="py-2">
              <div className="text-sm font-medium text-anthracite-100">{r.nom}</div>
              <div className="text-xs text-anthracite-400">{r.description}</div>
            </div>
          ))}
        </div>
      </div>

      <UsersSection users={users} reload={reloadAll} pushToast={pushToast} />

      <GroupsSection groups={groups} reload={reloadAll} pushToast={pushToast} />
      <PoolsSection pools={pools} vms={vms} reload={reloadAll} pushToast={pushToast} />
      <CustomRolesSection customRoles={customRoles} privileges={privileges} reload={reloadAll} pushToast={pushToast} />
      <AclSection acl={acl} roles={allRoles} groups={groups} pools={pools} vms={vms} users={users} reload={reloadAll} pushToast={pushToast} />
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
      pushToast({ kind: "success", title: "Rôle créé", message: name.trim() });
      setName(""); setSelected({});
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de création", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id, roleName) {
    setBusy(true);
    try {
      await deleteCustomRole(id);
      pushToast({ kind: "success", title: "Rôle supprimé", message: roleName });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  const ready = customRoles && privileges;
  const selectedCount = Object.values(selected).filter(Boolean).length;

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck size={15} className="text-anthracite-300" />
        <h3 className="text-sm font-semibold text-anthracite-100">Rôles personnalisés</h3>
      </div>
      <p className="text-xs text-anthracite-400 mb-3">Construis un rôle à la carte en choisissant exactement les actions autorisées, en plus de Lecteur/Opérateur/Gestionnaire.</p>

      {!ready ? (
        <p className="text-sm text-anthracite-400">Chargement...</p>
      ) : (
        <>
          <div className="rounded-md border border-anthracite-600 p-3 mb-3">
            <input className="input mb-2" placeholder="Nom du rôle (ex. sauvegardes-seules)" value={name} onChange={(e) => setName(e.target.value)} />
            <div className="grid grid-cols-1 gap-1.5 mb-2 sm:grid-cols-2">
              {Object.entries(privileges).map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 text-xs text-anthracite-200 cursor-pointer">
                  <input type="checkbox" checked={!!selected[key]} onChange={() => toggle(key)} />
                  {label}
                </label>
              ))}
            </div>
            <button className="btn-primary" disabled={busy || !name.trim() || selectedCount === 0} onClick={handleCreate}>
              <Plus size={14} /> Créer ({selectedCount} privilège{selectedCount > 1 ? "s" : ""})
            </button>
          </div>

          {customRoles.length === 0 ? (
            <p className="text-sm text-anthracite-400">Aucun rôle personnalisé.</p>
          ) : (
            <div className="divide-y divide-anthracite-600">
              {customRoles.map((r) => (
                <div key={r.key} className="flex items-center justify-between py-2 text-sm">
                  <div>
                    <span className="text-anthracite-100 font-medium">{r.label}</span>
                    <span className="text-anthracite-400"> -- {[...r.privileges].map((p) => privileges[p] || p).join(", ")}</span>
                  </div>
                  <button className="text-anthracite-400 hover:text-status-error" disabled={busy} onClick={() => handleDelete(r.id, r.label)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
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
      pushToast({ kind: "success", title: "Utilisateur créé", message: newUsername.trim() });
      setNewUsername(""); setNewPassword(""); setNewRole("observateur");
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de création", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleRoleChange(username, role) {
    setBusy(true);
    try {
      await updateUser(username, { role });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(username) {
    if (!window.confirm(`Supprimer l'utilisateur '${username}' ?`)) return;
    setBusy(true);
    try {
      await deleteUser(username);
      pushToast({ kind: "success", title: "Utilisateur supprimé", message: username });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-3">
        <UserPlus size={15} className="text-anthracite-300" />
        <h3 className="text-sm font-semibold text-anthracite-100">Utilisateurs</h3>
      </div>

      <div className="grid grid-cols-1 gap-2 mb-3 sm:grid-cols-4">
        <input className="input" placeholder="Nom d'utilisateur" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} />
        <input className="input" type="password" placeholder="Mot de passe (min. 4)" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        <select className="input" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
          <option value="observateur">observateur</option>
          <option value="admin">admin</option>
        </select>
        <button className="btn-primary" disabled={busy || !newUsername.trim() || newPassword.length < 4} onClick={handleCreate}>
          <Plus size={14} /> Créer
        </button>
      </div>

      {users == null ? (
        <p className="text-sm text-anthracite-400">Chargement...</p>
      ) : (
        <div className="divide-y divide-anthracite-600">
          {users.map((u) => (
            <div key={u.username} className="flex items-center justify-between py-2 text-sm">
              <span className="text-anthracite-100">{u.username}{u.username === me && <span className="text-anthracite-500"> (toi)</span>}</span>
              <div className="flex items-center gap-2">
                <select
                  className="input text-xs py-1 w-auto"
                  value={u.role}
                  disabled={busy || u.username === me}
                  onChange={(e) => handleRoleChange(u.username, e.target.value)}
                >
                  <option value="observateur">observateur</option>
                  <option value="admin">admin</option>
                </select>
                <button
                  className="text-anthracite-400 hover:text-status-error disabled:opacity-30 disabled:hover:text-anthracite-400"
                  disabled={busy || u.username === me}
                  title={u.username === me ? "Impossible de te supprimer toi-même" : "Supprimer"}
                  onClick={() => handleDelete(u.username)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
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
      pushToast({ kind: "success", title: "Groupe créé", message: newName.trim() });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de création", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id, name) {
    setBusy(true);
    try {
      await deleteGroup(id);
      pushToast({ kind: "success", title: "Groupe supprimé", message: name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
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
      pushToast({ kind: "error", title: "Échec d'ajout", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleRemoveMember(id, username) {
    setBusy(true);
    try {
      await removeGroupMember(id, username);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de retrait", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Users size={15} className="text-anthracite-300" />
        <h3 className="text-sm font-semibold text-anthracite-100">Groupes d'utilisateurs</h3>
      </div>

      <div className="flex gap-2 mb-3">
        <input className="input" placeholder="Nom du groupe (ex. devs)" value={newName} onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()} />
        <button className="btn-primary shrink-0" disabled={busy || !newName.trim()} onClick={handleCreate}>
          <Plus size={14} /> Créer
        </button>
      </div>

      {groups == null ? (
        <p className="text-sm text-anthracite-400">Chargement...</p>
      ) : groups.length === 0 ? (
        <p className="text-sm text-anthracite-400">Aucun groupe. Crée un groupe pour attribuer des droits à plusieurs utilisateurs à la fois.</p>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => (
            <div key={g.id} className="rounded-md border border-anthracite-600 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-anthracite-100">{g.name}</span>
                <button className="text-anthracite-400 hover:text-status-error" disabled={busy} onClick={() => handleDelete(g.id, g.name)}>
                  <Trash2 size={14} />
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {g.membres.length === 0 && <span className="text-xs text-anthracite-500">Aucun membre</span>}
                {g.membres.map((m) => (
                  <span key={m} className="flex items-center gap-1 rounded bg-anthracite-700 px-2 py-0.5 text-xs text-anthracite-100">
                    {m}
                    <button className="text-anthracite-400 hover:text-status-error" onClick={() => handleRemoveMember(g.id, m)}>x</button>
                  </span>
                ))}
              </div>
              <div className="flex gap-1.5">
                <input className="input text-xs py-1" placeholder="nom d'utilisateur" value={memberInputs[g.id] || ""}
                  onChange={(e) => setMemberInputs((s) => ({ ...s, [g.id]: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && handleAddMember(g.id)} />
                <button className="btn-secondary text-xs py-1 shrink-0" disabled={busy} onClick={() => handleAddMember(g.id)}>Ajouter</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
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
      pushToast({ kind: "success", title: "Pool créé", message: newName.trim() });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de création", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id, name) {
    setBusy(true);
    try {
      await deletePool(id);
      pushToast({ kind: "success", title: "Pool supprimé", message: name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
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
      pushToast({ kind: "error", title: "Échec d'ajout", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleRemoveVm(id, vmName) {
    setBusy(true);
    try {
      await removePoolMember(id, vmName);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de retrait", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Boxes size={15} className="text-anthracite-300" />
        <h3 className="text-sm font-semibold text-anthracite-100">Pools de VM</h3>
      </div>
      <p className="text-xs text-anthracite-400 mb-3">Regroupe des VM (ex. "Projet-A") pour leur attribuer des droits d'un coup, sans les lister une par une.</p>

      <div className="flex gap-2 mb-3">
        <input className="input" placeholder="Nom du pool (ex. projet-a)" value={newName} onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()} />
        <button className="btn-primary shrink-0" disabled={busy || !newName.trim()} onClick={handleCreate}>
          <Plus size={14} /> Créer
        </button>
      </div>

      {pools == null ? (
        <p className="text-sm text-anthracite-400">Chargement...</p>
      ) : pools.length === 0 ? (
        <p className="text-sm text-anthracite-400">Aucun pool.</p>
      ) : (
        <div className="space-y-3">
          {pools.map((p) => {
            const available = vms.filter((v) => !p.vms.includes(v.nom));
            return (
              <div key={p.id} className="rounded-md border border-anthracite-600 p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-anthracite-100">{p.name}</span>
                  <button className="text-anthracite-400 hover:text-status-error" disabled={busy} onClick={() => handleDelete(p.id, p.name)}>
                    <Trash2 size={14} />
                  </button>
                </div>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {p.vms.length === 0 && <span className="text-xs text-anthracite-500">Aucune VM</span>}
                  {p.vms.map((v) => (
                    <span key={v} className="flex items-center gap-1 rounded bg-anthracite-700 px-2 py-0.5 text-xs text-anthracite-100">
                      {v}
                      <button className="text-anthracite-400 hover:text-status-error" onClick={() => handleRemoveVm(p.id, v)}>x</button>
                    </span>
                  ))}
                </div>
                {available.length > 0 && (
                  <div className="flex gap-1.5">
                    <select className="input text-xs py-1" value={vmSelect[p.id] || ""} onChange={(e) => setVmSelect((s) => ({ ...s, [p.id]: e.target.value }))}>
                      <option value="">Choisir une VM...</option>
                      {available.map((v) => <option key={v.nom} value={v.nom}>{v.nom}</option>)}
                    </select>
                    <button className="btn-secondary text-xs py-1 shrink-0" disabled={busy || !vmSelect[p.id]} onClick={() => handleAddVm(p.id)}>Ajouter</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AclSection({ acl, roles, groups, pools, vms, users, reload, pushToast }) {
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
      pushToast({ kind: "success", title: "Attribution créée" });
      setSubjectId(""); setResourceId("");
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de l'attribution", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(id) {
    setBusy(true);
    try {
      await deleteAcl(id);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-3">
        <ShieldCheck size={15} className="text-anthracite-300" />
        <h3 className="text-sm font-semibold text-anthracite-100">Attributions (qui a quel rôle, sur quoi)</h3>
      </div>

      {!ready ? (
        <p className="text-sm text-anthracite-400">Chargement...</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 mb-2 sm:grid-cols-4">
            <div>
              <label className="text-[11px] text-anthracite-400">Qui</label>
              <select className="input text-xs py-1.5" value={subjectType} onChange={(e) => { setSubjectType(e.target.value); setSubjectId(""); }}>
                <option value="user">Utilisateur</option>
                <option value="group">Groupe</option>
              </select>
            </div>
            <div>
              <label className="text-[11px] text-anthracite-400">&nbsp;</label>
              <select className="input text-xs py-1.5" value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
                <option value="">Choisir...</option>
                {subjectType === "user"
                  ? users.map((u) => <option key={u.username} value={u.username}>{u.username}</option>)
                  : groups.map((g) => <option key={g.id} value={String(g.id)}>{g.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[11px] text-anthracite-400">Rôle</label>
              <select className="input text-xs py-1.5" value={role} onChange={(e) => setRole(e.target.value)}>
                {Object.entries(roles).map(([key, r]) => <option key={key} value={key}>{r.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[11px] text-anthracite-400">Sur</label>
              <select className="input text-xs py-1.5" value={resourceType} onChange={(e) => { setResourceType(e.target.value); setResourceId(""); }}>
                <option value="vm">Une VM</option>
                <option value="pool">Un pool</option>
              </select>
            </div>
          </div>
          <div className="flex gap-2 mb-3">
            <select className="input" value={resourceId} onChange={(e) => setResourceId(e.target.value)}>
              <option value="">{resourceType === "vm" ? "Choisir une VM..." : "Choisir un pool..."}</option>
              {resourceType === "vm"
                ? vms.map((v) => <option key={v.nom} value={v.nom}>{v.nom}</option>)
                : pools.map((p) => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
            </select>
            <button className="btn-primary shrink-0" disabled={busy || !subjectId || !resourceId} onClick={handleCreate}>
              <Plus size={14} /> Attribuer
            </button>
          </div>

          {roles[role] && (
            <p className="text-[11px] text-anthracite-500 mb-3">{roles[role].description}</p>
          )}

          {acl && acl.length > 0 ? (
            <div className="divide-y divide-anthracite-600">
              {acl.map((a) => (
                <div key={a.id} className="flex items-center justify-between py-2 text-sm">
                  <div className="text-anthracite-100">
                    <span className="font-medium">{a.subject_type === "group" ? `Groupe ${a.subject_label}` : a.subject_label}</span>
                    <span className="text-anthracite-400"> -- {roles[a.role]?.label || a.role} -- </span>
                    <span>{a.resource_type === "pool" ? `Pool ${a.resource_label}` : a.resource_label}</span>
                  </div>
                  <button className="text-anthracite-400 hover:text-status-error" disabled={busy} onClick={() => handleDelete(a.id)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-anthracite-400">Aucune attribution -- les accès restent limités aux rôles globaux ci-dessus.</p>
          )}
        </>
      )}
    </div>
  );
}
