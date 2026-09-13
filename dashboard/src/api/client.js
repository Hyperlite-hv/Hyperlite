// Point d'entree unique pour toutes les donnees de l'app. Tout vient du vrai
// backend Hyperlite (memes chemins que les routes FastAPI reelles, voir
// vite.config.js pour le proxy de dev). Hyperlite ne gere qu'un seul host
// (pas de cluster) et aucun conteneur LXC : fetchNodes() renvoie donc toujours
// un seul noeud reel, construit a partir de GET /dashboard.

let taskIdCounter = 0;
export function makeTaskId() {
  taskIdCounter += 1;
  return `task-${Date.now()}-${taskIdCounter}`;
}

let token = null;
export function setAuthToken(t) {
  token = t;
}
export function getAuthToken() {
  return token;
}

async function realFetch(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers });
  let data = null;
  try { data = await res.json(); } catch (e) { /* pas de corps JSON */ }
  if (!res.ok) {
    const msg = (data && data.detail) ? (Array.isArray(data.detail) ? data.detail.join(" ; ") : data.detail) : "Erreur inconnue";
    throw new Error(msg);
  }
  return data;
}

function jsonBody(payload) {
  return { headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}

export async function fetchDashboardSummary() {
  return realFetch("/dashboard");
}

export async function fetchNodes() {
  const d = await fetchDashboardSummary();
  const node = {
    id: "kvm-lab",
    nom: d.hyperviseur.nom,
    etat: d.hyperviseur.connecte ? "online" : "erreur",
    // CPU/RAM totale non exposees par GET /dashboard aujourd'hui -- valeurs
    // laissees a null, affichees en degrade (voir NodeSummaryTab).
    cpu_coeurs: null,
    cpu_utilisation: null,
    memoire_totale_mo: null,
    memoire_utilisee_mo: null,
    memoire_disponible_mo: d.memoire_disponible_mo,
    stockage_total_go: d.stockage.capacite_go,
    stockage_utilise_go: d.stockage.capacite_go != null && d.stockage.disponible_go != null
      ? Math.round((d.stockage.capacite_go - d.stockage.disponible_go) * 100) / 100 : null,
    uptime_s: d.hyperviseur.uptime_s,
    ip: null,
    version: `Hyperlite (${d.hyperviseur.type})`,
    os: null,
    vms_actives: d.vms.actives,
    vms_arretees: d.vms.arretees,
  };
  return [node];
}

export async function fetchVMs() {
  const vms = await realFetch("/vms");
  // GET /vms ne renvoie pas encore toutes les stats affichees par ce dashboard
  // (disque detaille, tags...) -- completees par des valeurs par defaut en
  // attendant un GET /vms plus riche.
  return vms.map((v) => ({
    nom: v.nom, node: "kvm-lab", type: "vm", etat: v.etat,
    vcpu: v.vcpu, memoire_mo: v.memoire_mo, memoire_utilisee_mo: null,
    disque_go: null, disque_utilise_go: null,
    ip: v.ip, utilisateur_ssh: v.utilisateur_ssh, uuid: v.uuid,
    os: v.os, uptime_s: v.uptime_s,
  }));
}

export async function fetchStoragePools() {
  const pools = await realFetch("/storage");
  return pools.map((p) => ({ nom: p.nom, node: "kvm-lab", type: "dir", etat: p.etat, capacite_go: p.capacite_go, disponible_go: p.disponible_go }));
}

export async function fetchNetworks() {
  const nets = await realFetch("/networks");
  return nets.map((n) => ({ nom: n.nom, type: n.type, pont: n.pont, actif: n.actif, reseau: n.reseau }));
}
export async function fetchNetworkDetail(name) {
  return realFetch(`/networks/${encodeURIComponent(name)}`);
}
export async function createNetwork(payload) {
  return realFetch("/networks", { method: "POST", ...jsonBody(payload) });
}
export async function deleteNetwork(name) {
  return realFetch(`/networks/${encodeURIComponent(name)}?confirm=true`, { method: "DELETE" });
}

// ---- Pare-feu par VM (réel : GET/PUT /vms/{name}/firewall, nwfilter libvirt) ----
export async function fetchVMFirewall(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/firewall`);
}
export async function setVMFirewall(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/firewall`, { method: "PUT", ...jsonBody(payload) });
}

// ---- Backups natifs (réel : GET/POST /vms/{name}/backups, DELETE /backups/{id},
// POST /backups/{id}/restore, GET/PUT/DELETE /vms/{name}/backup-schedule --
// voir app/routers/backups.py, chantier 13) ----
export async function fetchAllBackups() {
  return realFetch("/backups");
}
export async function fetchVMBackups(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backups`);
}
export async function createBackup(name, targetDir = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backups`, { method: "POST", ...jsonBody({ target_dir: targetDir }) });
}
export async function deleteBackup(id) {
  return realFetch(`/backups/${id}?confirm=true`, { method: "DELETE" });
}
export async function restoreBackup(id, mode, newName = null) {
  return realFetch(`/backups/${id}/restore`, { method: "POST", ...jsonBody({ mode, new_name: newName }) });
}
export async function fetchBackupSchedule(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backup-schedule`);
}
export async function setBackupSchedule(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backup-schedule`, { method: "PUT", ...jsonBody(payload) });
}
export async function deleteBackupSchedule(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backup-schedule`, { method: "DELETE" });
}

export async function fetchIsoTemplates() {
  return realFetch("/isos");
}
export async function deleteIso(filename) {
  return realFetch(`/isos/${encodeURIComponent(filename)}?confirm=true`, { method: "DELETE" });
}

// ---- Journal d'audit (reel : table audit_log, alimentee par chaque action) ----
export async function fetchAuditLog(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, v);
  });
  const qs = params.toString();
  return realFetch(`/audit${qs ? `?${qs}` : ""}`);
}
export async function fetchAuditActions() {
  return realFetch("/audit/actions");
}

// ---- Taches persistees (reel : table tasks, horodatage creation/debut/fin -
// voir app/core/tasks.py). Remplace le fetchTasks() encore theorique referme
// dans NodeTasksTab.jsx par un vrai GET /tasks filtrable/triable.
export async function fetchTasks(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, v);
  });
  const qs = params.toString();
  return realFetch(`/tasks${qs ? `?${qs}` : ""}`);
}
export async function fetchTaskDetail(id) {
  return realFetch(`/tasks/${encodeURIComponent(id)}`);
}

// ---- Mise à jour d'Hyperlite depuis Git (réel : GET/POST /update/*, voir
// app/routers/update.py — chantier 7) ----
export async function fetchUpdateCheck() {
  return realFetch("/update/check");
}
export async function applyUpdate() {
  return realFetch("/update/apply", { method: "POST" });
}

// ---- Utilisateurs (reels) ----
export async function fetchUsers() {
  return realFetch("/auth/users");
}
export async function createUser(username, password, role) {
  return realFetch("/auth/users", { method: "POST", ...jsonBody({ username, password, role }) });
}
export async function updateUser(username, payload) {
  return realFetch(`/auth/users/${encodeURIComponent(username)}`, { method: "PATCH", ...jsonBody(payload) });
}
export async function deleteUser(username) {
  return realFetch(`/auth/users/${encodeURIComponent(username)}`, { method: "DELETE" });
}

// ---- Actions VM (endpoints reels) ----
export async function startVM(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/start`, { method: "POST" });
}
export async function stopVM(name, force = false) {
  return realFetch(`/vms/${encodeURIComponent(name)}/stop?force=${force}`, { method: "POST" });
}
export async function restartVM(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/restart?force=true`, { method: "POST" });
}
export async function deleteVM(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}?confirm=true`, { method: "DELETE" });
}
export async function cloneVM(name, newName) {
  return realFetch(`/vms/${encodeURIComponent(name)}/clone`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
export async function createVM(payload) {
  return realFetch("/vms", { method: "POST", ...jsonBody(payload) });
}
export async function updateVM(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}`, { method: "PATCH", ...jsonBody(payload) });
}
export async function fetchVM(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}`);
}

// ---- Limites/réservations de ressources (réel : GET/PUT /vms/{name}/limits,
// cgroups via libvirt schedulerParametersFlags/memoryParameters) ----
export async function fetchVMLimits(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/limits`);
}
export async function setVMLimits(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/limits`, { method: "PUT", ...jsonBody(payload) });
}
export async function fetchVMMetrics(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/metrics`);
}
export async function fetchVMMetricsHistory(name, range = "1h") {
  return realFetch(`/vms/${encodeURIComponent(name)}/metrics/history?range=${encodeURIComponent(range)}`);
}
export async function fetchHostMetricsHistory(range = "1h") {
  return realFetch(`/host/metrics/history?range=${encodeURIComponent(range)}`);
}
export async function fetchProvisioningStatus(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/provisioning`);
}
export async function fetchVMDisks(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks`);
}
export async function attachDisk(name, volumeName, targetDev, pool = "default") {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks`, { method: "POST", ...jsonBody({ volume_name: volumeName, pool, target_dev: targetDev }) });
}
export async function detachDisk(name, targetDev) {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks/${encodeURIComponent(targetDev)}`, { method: "DELETE" });
}
export async function fetchVMNetwork(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/network`);
}
export async function attachInterface(name, network, vlanTag = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/interfaces`, { method: "POST", ...jsonBody({ network, vlan_tag: vlanTag }) });
}
export async function detachInterface(name, mac) {
  return realFetch(`/vms/${encodeURIComponent(name)}/interfaces/${encodeURIComponent(mac)}`, { method: "DELETE" });
}
export async function createVolume(pool, name, sizeGb) {
  return realFetch(`/storage/${encodeURIComponent(pool)}/volumes`, { method: "POST", ...jsonBody({ name, size_gb: sizeGb }) });
}
export async function fetchVolumes(pool) {
  return realFetch(`/storage/${encodeURIComponent(pool)}/volumes`);
}

// ---- Console VNC / Terminal SSH (relais WebSocket reels) ----
export async function createConsoleTicket(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/console-ticket`, { method: "POST" });
}
export async function createTerminalTicket(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/terminal-ticket`, { method: "POST" });
}

// ---- Shell interactif sur l'hôte physique (admin uniquement, voir app/routers/host.py) ----
export async function createHostTerminalTicket() {
  return realFetch("/host/terminal-ticket", { method: "POST" });
}

// ---- Snapshots (reels) ----
export async function fetchSnapshots(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/snapshots`);
}
export async function createSnapshot(name, snapshotName, description) {
  return realFetch(`/vms/${encodeURIComponent(name)}/snapshots`, { method: "POST", ...jsonBody({ name: snapshotName, description }) });
}
export async function restoreSnapshot(name, snapName) {
  return realFetch(`/vms/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(snapName)}/restore?confirm=true`, { method: "POST" });
}
export async function deleteSnapshot(name, snapName) {
  return realFetch(`/vms/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(snapName)}`, { method: "DELETE" });
}

// ---- Templates (reels) ----
export async function fetchTemplates() {
  return realFetch("/templates");
}
export async function createTemplateFromVM(vmName, templateName) {
  return realFetch(`/templates/from-vm/${encodeURIComponent(vmName)}`, { method: "POST", ...jsonBody({ template_name: templateName || null }) });
}
export async function deployTemplate(templateName, newName, network) {
  return realFetch(`/templates/${encodeURIComponent(templateName)}/deploy`, { method: "POST", ...jsonBody({ new_name: newName, network: network || null }) });
}
export async function deleteTemplate(templateName) {
  return realFetch(`/templates/${encodeURIComponent(templateName)}?confirm=true`, { method: "DELETE" });
}

// ---- Permissions granulaires (reels) : Groupes, Pools, ACL ----
export async function fetchGroups() {
  return realFetch("/groups");
}
export async function createGroup(name) {
  return realFetch("/groups", { method: "POST", ...jsonBody({ name }) });
}
export async function deleteGroup(groupId) {
  return realFetch(`/groups/${groupId}`, { method: "DELETE" });
}
export async function addGroupMember(groupId, username) {
  return realFetch(`/groups/${groupId}/members`, { method: "POST", ...jsonBody({ username }) });
}
export async function removeGroupMember(groupId, username) {
  return realFetch(`/groups/${groupId}/members/${encodeURIComponent(username)}`, { method: "DELETE" });
}

export async function fetchPools() {
  return realFetch("/pools");
}
export async function createPool(name, description = "") {
  return realFetch("/pools", { method: "POST", ...jsonBody({ name, description }) });
}
export async function deletePool(poolId) {
  return realFetch(`/pools/${poolId}`, { method: "DELETE" });
}
export async function addPoolMember(poolId, vmName) {
  return realFetch(`/pools/${poolId}/members`, { method: "POST", ...jsonBody({ vm_name: vmName }) });
}
export async function removePoolMember(poolId, vmName) {
  return realFetch(`/pools/${poolId}/members/${encodeURIComponent(vmName)}`, { method: "DELETE" });
}

export async function fetchAclRoles() {
  return realFetch("/acl/roles");
}
export async function fetchAcl() {
  return realFetch("/acl");
}
export async function createAcl(payload) {
  return realFetch("/acl", { method: "POST", ...jsonBody(payload) });
}
export async function deleteAcl(aclId) {
  return realFetch(`/acl/${aclId}`, { method: "DELETE" });
}

export async function fetchPrivileges() {
  return realFetch("/acl/privileges");
}
export async function fetchCustomRoles() {
  return realFetch("/acl/custom-roles");
}
export async function createCustomRole(name, privileges) {
  return realFetch("/acl/custom-roles", { method: "POST", ...jsonBody({ name, privileges }) });
}
export async function deleteCustomRole(roleId) {
  return realFetch(`/acl/custom-roles/${roleId}`, { method: "DELETE" });
}
