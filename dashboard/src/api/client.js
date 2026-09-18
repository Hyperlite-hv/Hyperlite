// Point d'entree unique pour toutes les donnees de l'app. Tout vient du vrai
// backend Hyperlite (memes chemins que les routes FastAPI reelles, voir
// vite.config.js pour le proxy de dev).

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
  const localNode = {
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

  // Noeuds distants enregistres (chantier 15) -- AUPARAVANT absents d'ici :
  // cette fonction ne renvoyait toujours qu'un unique noeud synthetique
  // "kvm-lab", donc un noeud distant reellement enregistre et fonctionnel
  // cote backend (GET /nodes) restait invisible dans l'arbre principal --
  // bug reel signale en testant un vrai second noeud physique (seul
  // l'onglet dedie "Noeuds", qui interroge /nodes directement, le montrait).
  let remoteNodes = [];
  try {
    const remotes = await fetchRemoteNodes();
    remoteNodes = await Promise.all(remotes.map(async (n) => {
      let s = null;
      try { s = await fetchRemoteNodeSummary(n.name); } catch (e) { /* noeud injoignable pour l'instant -- degrade plutot que de faire echouer tout le tableau de bord */ }
      return {
        id: n.name,
        nom: n.name,
        etat: s ? (s.connecte ? "online" : "erreur") : (n.statut === "en_ligne" ? "online" : "erreur"),
        cpu_coeurs: null,
        cpu_utilisation: null,
        memoire_totale_mo: null,
        memoire_utilisee_mo: null,
        memoire_disponible_mo: null,
        stockage_total_go: s?.stockage_capacite_go ?? null,
        stockage_utilise_go: s && s.stockage_capacite_go != null && s.stockage_disponible_go != null
          ? Math.round((s.stockage_capacite_go - s.stockage_disponible_go) * 100) / 100 : null,
        uptime_s: null,
        ip: n.hostname,
        version: "Hyperlite (distant)",
        os: null,
        vms_actives: s?.vms_actives ?? 0,
        vms_arretees: s?.vms_arretees ?? 0,
        distant: true,
      };
    }));
  } catch (e) { /* GET /nodes indisponible -- reste sur le noeud local seul, comme avant ce correctif */ }

  return [localNode, ...remoteNodes];
}

// GET /vms ne renvoie pas encore toutes les stats affichees par ce dashboard
// (disque detaille, tags...) -- completees par des valeurs par defaut en
// attendant un GET /vms plus riche.
function mapVm(v, nodeId) {
  return {
    nom: v.nom, node: nodeId, type: "vm", etat: v.etat,
    vcpu: v.vcpu, memoire_mo: v.memoire_mo, memoire_utilisee_mo: null,
    disque_go: null, disque_utilise_go: null,
    ip: v.ip, utilisateur_ssh: v.utilisateur_ssh, uuid: v.uuid,
    os: v.os, uptime_s: v.uptime_s,
    // BUG REEL trouve en testant l'onglet Snapshots d'une VM sur pool
    // ZFS dans un vrai navigateur (backlog stockage 2026-09-18, phase 3) :
    // ce mappage whitelistait les champs sans stockage_zfs (ajoute cote
    // backend, app/routers/vms.py::_domain_summary), donc silencieusement
    // perdu ici -- VMSnapshotsTab.jsx recevait toujours `undefined`.
    stockage_zfs: v.stockage_zfs,
  };
}

export async function fetchVMs() {
  const localVms = await realFetch("/vms");
  let result = localVms.map((v) => mapVm(v, "kvm-lab"));

  // Noeuds distants enregistres (chantier 15) -- AUPARAVANT jamais
  // interroges ici (node force en dur a "kvm-lab" pour tout le monde), donc
  // les VM d'un noeud distant n'apparaissaient jamais dans l'arbre
  // principal malgre un enregistrement reussi cote backend -- bug reel
  // signale en testant un vrai second noeud physique. GET /vms accepte
  // maintenant un parametre node= (voir app/routers/vms.py).
  try {
    const remotes = await fetchRemoteNodes();
    const remoteLists = await Promise.all(remotes.map(async (n) => {
      try {
        const vms = await realFetch(`/vms?node=${encodeURIComponent(n.name)}`);
        return vms.map((v) => mapVm(v, n.name));
      } catch (e) { return []; /* noeud injoignable pour l'instant */ }
    }));
    result = result.concat(...remoteLists);
  } catch (e) { /* GET /nodes indisponible -- reste sur le noeud local seul */ }

  return result;
}

function mapPool(p, nodeId) {
  // BUG REEL trouve le 2026-09-17 (chantier 26, pools NFS) : `type`
  // etait code en dur a "dir" ici -- inoffensif tant que GET /storage ne
  // renvoyait jamais de vrai champ `type` (tous les pools existants
  // etaient effectivement "dir"), mais aurait masque silencieusement le
  // nouveau champ reel une fois les pools NFS ajoutes cote backend.
  return { nom: p.nom, node: nodeId, type: p.type, etat: p.etat, capacite_go: p.capacite_go, disponible_go: p.disponible_go };
}

export async function fetchStoragePools() {
  const localPools = await realFetch("/storage");
  let result = localPools.map((p) => mapPool(p, "kvm-lab"));

  try {
    const remotes = await fetchRemoteNodes();
    const remoteLists = await Promise.all(remotes.map(async (n) => {
      try {
        const pools = await realFetch(`/storage?node=${encodeURIComponent(n.name)}`);
        return pools.map((p) => mapPool(p, n.name));
      } catch (e) { return []; }
    }));
    result = result.concat(...remoteLists);
  } catch (e) { /* GET /nodes indisponible */ }

  return result;
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

// ---- Pare-feu réseau (chantier 21, réel : GET/PUT /networks/{name}/firewall,
// distinct du pare-feu par VM -- filtre au niveau du pont, pas de l'interface) ----
export async function fetchNetworkFirewall(name) {
  return realFetch(`/networks/${encodeURIComponent(name)}/firewall`);
}
export async function setNetworkFirewall(name, payload) {
  return realFetch(`/networks/${encodeURIComponent(name)}/firewall`, { method: "PUT", ...jsonBody(payload) });
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

// ---- Disques importables (chantier 23 : import de VM depuis un fichier disque) ----
export async function fetchVmDisks() {
  return realFetch("/vm-disks");
}
export async function deleteVmDisk(filename) {
  return realFetch(`/vm-disks/${encodeURIComponent(filename)}`, { method: "DELETE" });
}

// ---- Export de VM (chantier 23) ----
export async function fetchVmExports() {
  return realFetch("/vm-exports");
}
export async function exportVM(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/export`, { method: "POST" });
}
export async function deleteVmExport(filename) {
  return realFetch(`/vm-exports/${encodeURIComponent(filename)}`, { method: "DELETE" });
}
export async function downloadVmExport(filename) {
  const { ticket } = await realFetch(`/vm-exports/${encodeURIComponent(filename)}/download-ticket`, { method: "POST" });
  window.open(`/vm-exports/download?ticket=${encodeURIComponent(ticket)}`, "_blank");
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

// ---- Automation : moteur de Jobs (réel : /jobs, voir app/routers/jobs.py, chantier 14) ----
export async function fetchJobs() {
  return realFetch("/jobs");
}
export async function fetchJob(id) {
  return realFetch(`/jobs/${id}`);
}
export async function createJob(payload) {
  return realFetch("/jobs", { method: "POST", ...jsonBody(payload) });
}
export async function deleteJob(id) {
  return realFetch(`/jobs/${id}`, { method: "DELETE" });
}
export async function runJob(id, targets, dryRun) {
  return realFetch(`/jobs/${id}/run`, { method: "POST", ...jsonBody({ targets, dry_run: dryRun }) });
}
export async function fetchJobRuns(id) {
  return realFetch(`/jobs/${id}/runs`);
}
export async function fetchJobRun(runId) {
  return realFetch(`/jobs/runs/${runId}`);
}

// ---- Multi-nœuds (réel : /nodes, voir app/routers/nodes.py, chantier 15) ----
export async function fetchRemoteNodes() {
  return realFetch("/nodes");
}
export async function fetchClusterPubkey() {
  return realFetch("/nodes/cluster-pubkey");
}
export async function addRemoteNode(payload) {
  return realFetch("/nodes", { method: "POST", ...jsonBody(payload) });
}
export async function fetchRemoteNodeSummary(name) {
  return realFetch(`/nodes/${encodeURIComponent(name)}/summary`);
}
export async function deleteRemoteNode(name) {
  return realFetch(`/nodes/${encodeURIComponent(name)}`, { method: "DELETE" });
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
// node (backlog 2026-09-18, actions VM multi-nœuds) : "kvm-lab" ou omis =
// hôte local (comportement historique inchangé), sinon le nom d'un nœud
// distant enregistré -- même convention que fetchVMs()/migrateVM().
export async function startVM(name, node = null) {
  const q = node && node !== "kvm-lab" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/vms/${encodeURIComponent(name)}/start${q}`, { method: "POST" });
}
export async function stopVM(name, force = false, node = null) {
  const params = new URLSearchParams({ force: String(force) });
  if (node && node !== "kvm-lab") params.set("node", node);
  return realFetch(`/vms/${encodeURIComponent(name)}/stop?${params}`, { method: "POST" });
}
export async function restartVM(name, node = null) {
  const params = new URLSearchParams({ force: "true" });
  if (node && node !== "kvm-lab") params.set("node", node);
  return realFetch(`/vms/${encodeURIComponent(name)}/restart?${params}`, { method: "POST" });
}
export async function deleteVM(name, node = null) {
  const params = new URLSearchParams({ confirm: "true" });
  if (node && node !== "kvm-lab") params.set("node", node);
  return realFetch(`/vms/${encodeURIComponent(name)}?${params}`, { method: "DELETE" });
}
export async function cloneVM(name, newName) {
  return realFetch(`/vms/${encodeURIComponent(name)}/clone`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
// Chantier 27 (migration a chaud) : sourceNode "kvm-lab" (ou omis) = hote
// local, meme convention que le reste (open_conn(node), fetchVMs...).
export async function migrateVM(name, targetNode, sourceNode) {
  const qs = sourceNode && sourceNode !== "kvm-lab" ? `?node=${encodeURIComponent(sourceNode)}` : "";
  return realFetch(`/vms/${encodeURIComponent(name)}/migrate${qs}`, { method: "POST", ...jsonBody({ target_node: targetNode }) });
}
// Chantier 17 (HA) : voir app/core/ha.py -- pas de fencing, recuperation
// toujours declenchee par un admin, jamais automatique.
export async function fetchHaProtected() {
  return realFetch("/ha");
}
export async function enableHa(name, node) {
  return realFetch(`/ha/${encodeURIComponent(name)}/enable`, { method: "POST", ...jsonBody({ node: node && node !== "kvm-lab" ? node : null }) });
}
export async function disableHa(name) {
  return realFetch(`/ha/${encodeURIComponent(name)}`, { method: "DELETE" });
}
export async function recoverHa(name, targetNode) {
  return realFetch(`/ha/${encodeURIComponent(name)}/recover`, { method: "POST", ...jsonBody({ target_node: targetNode }) });
}

// Chantier 19 : suppression automatique des VM inactives (opt-in par VM,
// voir app/core/vm_cleanup.py -- le compteur ne court que pendant que la
// VM est arrêtée, jamais si elle est protégée HA).
export async function fetchVMAutoCleanup(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/auto-cleanup`);
}
export async function setVMAutoCleanup(name, inactiveDays) {
  return realFetch(`/vms/${encodeURIComponent(name)}/auto-cleanup`, { method: "PUT", ...jsonBody({ inactive_days: inactiveDays }) });
}
export async function disableVMAutoCleanup(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/auto-cleanup`, { method: "DELETE" });
}
// Chantier 28 (notifications sortantes) : voir app/core/notifications.py.
export async function fetchNotifyEvents() {
  return realFetch("/notifications/events");
}
export async function fetchNotificationChannels() {
  return realFetch("/notifications/channels");
}
export async function createNotificationChannel(payload) {
  return realFetch("/notifications/channels", { method: "POST", ...jsonBody(payload) });
}
export async function setNotificationChannelEnabled(id, enabled) {
  return realFetch(`/notifications/channels/${id}`, { method: "PATCH", ...jsonBody({ enabled }) });
}
export async function deleteNotificationChannel(id) {
  return realFetch(`/notifications/channels/${id}`, { method: "DELETE" });
}
export async function testNotificationChannel(id) {
  return realFetch(`/notifications/channels/${id}/test`, { method: "POST" });
}

// Chantier 20 (SSO OIDC) : voir app/core/sso.py / app/routers/sso.py.
// fetchSsoStatus() est appele SANS jeton (ecran de connexion, personne
// n'est encore authentifie) -- realFetch n'ajoute l'en-tete Authorization
// que si un token est present, donc reutilisable tel quel ici.
export async function fetchSsoStatus() {
  return realFetch("/auth/sso/status");
}
export async function fetchSsoConfig() {
  return realFetch("/auth/sso/config");
}
export async function updateSsoConfig(payload) {
  return realFetch("/auth/sso/config", { method: "PUT", ...jsonBody(payload) });
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
// Chantier 26 (stockage partage) : node optionnel, meme convention que le
// reste (fetchVMs, fetchStoragePools...) -- cree/supprime un pool sur un
// noeud distant enregistre plutot que l'hote local.
export async function createStoragePool(payload, node) {
  const qs = node ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/storage${qs}`, { method: "POST", ...jsonBody(payload) });
}
export async function deleteStoragePool(poolName, node) {
  const params = new URLSearchParams({ confirm: "true" });
  if (node) params.set("node", node);
  return realFetch(`/storage/${encodeURIComponent(poolName)}?${params.toString()}`, { method: "DELETE" });
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

// ---- Conteneurs LXC (reel : GET/POST/DELETE /containers, chantier 18) ----
export async function fetchContainers() {
  return realFetch("/containers");
}
export async function fetchContainer(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}`);
}
export async function searchDockerHub(query) {
  return realFetch(`/containers/docker-hub/search?q=${encodeURIComponent(query)}`);
}
export async function createContainer(payload) {
  return realFetch("/containers", { method: "POST", ...jsonBody(payload) });
}
export async function startContainer(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}/start`, { method: "POST" });
}
export async function stopContainer(name, force = false) {
  return realFetch(`/containers/${encodeURIComponent(name)}/stop?force=${force}`, { method: "POST" });
}
export async function deleteContainer(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}`, { method: "DELETE" });
}
export async function createContainerTerminalTicket(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}/terminal-ticket`, { method: "POST" });
}

// Backlog 2026-09-18 : clonage + sauvegarde/restauration de conteneur
// (pas de snapshot instantané possible, le pilote LXC de libvirt ne le
// supporte pas -- voir app/core/container_builder.py).
export async function cloneContainer(name, newName) {
  return realFetch(`/containers/${encodeURIComponent(name)}/clone`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
export async function fetchContainerBackups() {
  return realFetch("/containers/backups");
}
export async function createContainerBackup(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}/backups`, { method: "POST" });
}
export async function deleteContainerBackup(id) {
  return realFetch(`/containers/backups/${id}?confirm=true`, { method: "DELETE" });
}
export async function restoreContainerBackup(id, newName = null) {
  return realFetch(`/containers/backups/${id}/restore`, { method: "POST", ...jsonBody({ new_name: newName }) });
}

// Chantier 30 (2FA + jetons API, 2026-09-17) -- en libre-service, chaque
// utilisateur gere son propre compte (pas besoin d'etre admin).
export async function setup2FA() {
  return realFetch("/auth/2fa/setup", { method: "POST" });
}
export async function confirm2FA(code) {
  return realFetch("/auth/2fa/confirm", { method: "POST", ...jsonBody({ code }) });
}
export async function disable2FA(password) {
  return realFetch("/auth/2fa/disable", { method: "POST", ...jsonBody({ password }) });
}
export async function fetchApiTokens() {
  return realFetch("/auth/tokens");
}
export async function createApiToken(name) {
  return realFetch("/auth/tokens", { method: "POST", ...jsonBody({ name }) });
}
export async function deleteApiToken(id) {
  return realFetch(`/auth/tokens/${id}`, { method: "DELETE" });
}
