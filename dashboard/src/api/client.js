// Point d'entree unique pour toutes les donnees de l'app.
//
// USE_MOCK=false : la majorite des donnees vient desormais du vrai backend
// Hyperlite (memes chemins que les routes FastAPI reelles, voir vite.config.js
// pour le proxy de dev). Ce qui reste mock, faute d'equivalent backend :
//   - fetchNodes() : Hyperlite ne gere qu'un seul host (pas de route /nodes) --
//     on construit un noeud "reel" a partir de GET /dashboard, + on garde le
//     2e noeud fictif de mockData.js pour illustrer le multi-node.
//   - fetchContainers() : pas de conteneurs LXC cote backend.
//   - fetchTasks() : pas de route exposant l'audit_log SQLite existant.

import { nodes as mockNodes, containers as mockContainers, initialTasks, makeTaskId } from "./mockData";

const USE_MOCK = false;

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
  const fictifNode = mockNodes.find((n) => !n.reel);
  try {
    const d = await fetchDashboardSummary();
    const realNode = {
      id: "kvm-lab",
      nom: d.hyperviseur.nom,
      reel: true,
      etat: d.hyperviseur.connecte ? "online" : "erreur",
      // CPU/RAM totale non exposees par GET /dashboard aujourd'hui -- valeurs
      // approximatives pour que les jauges restent utilisables en attendant
      // un enrichissement cote backend (voir NodeSummaryTab pour le rendu degrade).
      cpu_coeurs: null,
      cpu_utilisation: null,
      memoire_totale_mo: null,
      memoire_utilisee_mo: null,
      memoire_disponible_mo: d.memoire_disponible_mo,
      stockage_total_go: d.stockage.capacite_go,
      stockage_utilise_go: d.stockage.capacite_go != null && d.stockage.disponible_go != null
        ? Math.round((d.stockage.capacite_go - d.stockage.disponible_go) * 100) / 100 : null,
      uptime_s: null,
      ip: null,
      version: `Hyperlite (${d.hyperviseur.type})`,
      os: null,
      vms_actives: d.vms.actives,
      vms_arretees: d.vms.arretees,
    };
    return [realNode, fictifNode];
  } catch (e) {
    return [fictifNode];
  }
}

export async function fetchVMs() {
  if (USE_MOCK) return [];
  const vms = await realFetch("/vms");
  // GET /vms ne renvoie pas encore toutes les stats affichees par ce dashboard
  // (disque/vcpu detailles, tags...) -- completees par des valeurs par defaut
  // en attendant un GET /vms plus riche, ou un second appel par VM si besoin.
  return vms.map((v) => ({
    nom: v.nom, node: "kvm-lab", type: "vm", etat: v.etat,
    vcpu: v.vcpu, memoire_mo: v.memoire_mo, memoire_utilisee_mo: null,
    disque_go: null, disque_utilise_go: null,
    ip: v.ip, utilisateur_ssh: v.utilisateur_ssh, uuid: v.uuid,
    os: null, uptime_s: null, tags: [],
  }));
}

export async function fetchContainers() {
  return mockContainers;
}

export async function fetchStoragePools() {
  const pools = await realFetch("/storage");
  return pools.map((p) => ({ nom: p.nom, node: "kvm-lab", type: "dir", etat: p.etat, capacite_go: p.capacite_go, disponible_go: p.disponible_go }));
}

export async function fetchNetworks() {
  const nets = await realFetch("/networks");
  return nets.map((n) => ({ nom: n.nom, type: n.type, pont: n.pont, actif: n.actif, reseau: n.reseau }));
}

export async function fetchIsoTemplates() {
  return realFetch("/isos");
}
export async function deleteIso(filename) {
  return realFetch(`/isos/${encodeURIComponent(filename)}?confirm=true`, { method: "DELETE" });
}

export async function fetchTasks() {
  return initialTasks;
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
export async function fetchVM(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}`);
}
export async function fetchVMMetrics(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/metrics`);
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
export async function attachInterface(name, network) {
  return realFetch(`/vms/${encodeURIComponent(name)}/interfaces`, { method: "POST", ...jsonBody({ network }) });
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

export { makeTaskId };
