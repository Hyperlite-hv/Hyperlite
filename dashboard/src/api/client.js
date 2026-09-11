// Point d'entree unique pour toutes les donnees de l'app. Aujourd'hui tout part
// de mockData.js (avec une latence simulee pour que les etats de chargement
// soient visibles). Pour brancher le vrai backend Hyperlite :
//   1. Passer USE_MOCK a false.
//   2. Renseigner un token JWT recupere via POST /auth/login (voir app/routers/auth.py)
//      -- ce dashboard n'a pas encore son propre ecran de login, a faire a ce moment-la.
//   3. Chaque fonction ci-dessous a deja son equivalent reel commente juste en dessous :
//      il suffit de decommenter, elles pointent vers les routes qui existent deja
//      (app/routers/vms.py, storage.py, network.py, isos.py).
// Le proxy Vite (/api -> 127.0.0.1:8000) est deja configure dans vite.config.js.

import { nodes, vms, containers, storagePools, networks, isoTemplates, initialTasks, makeTaskId } from "./mockData";

const USE_MOCK = true;
const LATENCE_MS = 250;

function delay(value, ms = LATENCE_MS) {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

let token = null;
export function setAuthToken(t) {
  token = t;
}

async function realFetch(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { ...opts, headers });
  let data = null;
  try { data = await res.json(); } catch (e) { /* pas de corps JSON */ }
  if (!res.ok) {
    const msg = (data && data.detail) ? (Array.isArray(data.detail) ? data.detail.join(" ; ") : data.detail) : "Erreur inconnue";
    throw new Error(msg);
  }
  return data;
}

export async function fetchNodes() {
  if (USE_MOCK) return delay(nodes);
  // return realFetch("/vms"); // Hyperlite n'expose qu'un seul host aujourd'hui,
  // pas de route /nodes -- a introduire cote backend pour le vrai multi-node.
}

export async function fetchVMs() {
  if (USE_MOCK) return delay(vms);
  // return realFetch("/vms"); // GET /vms existe deja, meme forme de champs.
}

export async function fetchContainers() {
  if (USE_MOCK) return delay(containers);
  // Aucune route reelle : Hyperlite ne gere pas les conteneurs LXC pour le moment.
  // return [];
}

export async function fetchStoragePools() {
  if (USE_MOCK) return delay(storagePools);
  // return realFetch("/storage");
}

export async function fetchNetworks() {
  if (USE_MOCK) return delay(networks);
  // return realFetch("/networks");
}

export async function fetchIsoTemplates() {
  if (USE_MOCK) return delay(isoTemplates);
  // return realFetch("/isos");
}

export async function fetchTasks() {
  if (USE_MOCK) return delay(initialTasks);
  // Pas encore de route dediee cote backend (l'audit_log SQLite existe mais n'est
  // pas expose via l'API) -- a ajouter pour remplacer le mock ici.
}

// Actions VM : chacune retourne { taskId } pour que le store puisse simuler/suivre
// une tache. Cote reel, la plupart de ces endpoints existent deja et repondent
// directement (pas de tache asynchrone cote serveur) -- le "taskId" ici est une
// construction du dashboard pour l'UX (barre de progression), pas du backend.
export async function startVM(name) {
  if (USE_MOCK) return delay({ taskId: makeTaskId() }, 150);
  // return realFetch(`/vms/${encodeURIComponent(name)}/start`, { method: "POST" });
}

export async function stopVM(name, force = false) {
  if (USE_MOCK) return delay({ taskId: makeTaskId() }, 150);
  // return realFetch(`/vms/${encodeURIComponent(name)}/stop?force=${force}`, { method: "POST" });
}

export async function restartVM(name) {
  if (USE_MOCK) return delay({ taskId: makeTaskId() }, 150);
  // return realFetch(`/vms/${encodeURIComponent(name)}/restart?force=true`, { method: "POST" });
}

export async function deleteVM(name) {
  if (USE_MOCK) return delay({ taskId: makeTaskId() }, 150);
  // return realFetch(`/vms/${encodeURIComponent(name)}?confirm=true`, { method: "DELETE" });
}

export async function createVM(payload) {
  if (USE_MOCK) return delay({ taskId: makeTaskId() }, 150);
  // return realFetch("/vms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}
