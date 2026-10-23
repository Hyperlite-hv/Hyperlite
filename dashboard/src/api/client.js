// Single entry point for all the application's data. Everything comes from the
// real Hyperlite backend (the same paths as the real FastAPI routes, see
// vite.config.js for the dev proxy).
import { apiPath } from "./prefix";

import { normalizeDetail } from "../next/lib/errors";

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

// Called when the backend rejects the session token (expired, revoked): the auth
// store registers a handler that returns the user to the sign-in page.
let unauthorizedHandler = null;
export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = fn;
}

const READ_TIMEOUT_MS = 30000;

// A list of the API (app/core/listing.py) is {items, total}: the screens use its items. A list that gathers several
// nodes (cluster=true) is read whole, for its "unreachable" and "stale" members.
async function realList(path, opts) {
  const data = await realFetch(path, opts);
  return data && Array.isArray(data.items) ? data.items : data;
}

const RELOADED_AT = "hyperlite.reloadedForApi";
export function reloadForNewerServer(now = Date.now(), storage = globalThis.sessionStorage, reload = () => globalThis.location?.reload()) {
  try {
    const last = Number(storage?.getItem(RELOADED_AT) || 0);
    if (now - last < 60000) return false;
    storage?.setItem(RELOADED_AT, String(now));
  } catch {
    return false; // no session storage: never reload, rather than risk a loop
  }
  reload();
  return true;
}

async function realFetch(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const sent = token;
  if (token) headers.Authorization = `Bearer ${token}`;
  // A read that never answers used to freeze the polling for good, with nothing saying the data went stale: reads
  // give up after READ_TIMEOUT_MS, so the failure shows (the stale-data banner) and the next tick tries again.
  // Changes (POST, PUT...) keep no limit: some legitimately run for minutes, and must not be retried blindly.
  const isRead = !opts.method || opts.method.toUpperCase() === "GET";
  const controller = isRead && !opts.signal ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), READ_TIMEOUT_MS) : null;
  let res;
  try {
    res = await fetch(apiPath(path), { ...opts, headers, ...(controller ? { signal: controller.signal } : {}) });
  } catch (e) {
    if (controller?.signal.aborted) throw new Error(`The server did not answer within ${READ_TIMEOUT_MS / 1000} s.`);
    if (e?.name === "AbortError") throw e;
    throw new Error("Cannot reach the server. Check your network connection and try again.");
  } finally {
    if (timer) clearTimeout(timer);
  }
  let data = null;
  let parsed = true;
  try { data = await res.json(); } catch { parsed = false; }
  // Only when the rejected token is still the current one: a request that left with a token replaced since
  // (right after a password change, which revokes the previous one) must not sign the user out.
  if (res.status === 401 && token && token === sent && !path.startsWith("/auth/login")) unauthorizedHandler?.();
  // The API moved (code api_gone): this page's code is older than the server's, as in a tab left open over an update
  // that changed the API. It reloads itself once instead of staying blank; at most once a minute, so a server that
  // keeps answering 410 cannot make it reload forever.
  if (res.status === 410 && data?.code === "api_gone") reloadForNewerServer();
  if (res.ok && !parsed && (res.headers.get("content-type") || "").includes("application/json")) {
    throw new Error("The server returned an unreadable response.");
  }
  if (!res.ok) {
    const msg = (data && data.detail) ? (normalizeDetail(data.detail) || "Unknown error") : "Unknown error";
    // The status lets a caller tell "this object does not exist (any more)" from a real failure.
    throw Object.assign(new Error(msg), { status: res.status });
  }
  return data;
}

// /health answers everyone but reports versions and host details only to a signed-in
// caller. Plain fetch rather than realFetch: it is polled while the service restarts,
// where a failure is expected and must not sign the user out.
// This node's system settings (local node, admin): package updates, DNS, time, remote syslog.
export async function fetchHostUpdates(refresh = false) {
  return realFetch(`/host/system/updates${refresh ? "?refresh=true" : ""}`);
}
export async function fetchAutoUpdates() {
  return realFetch("/host/system/auto-updates");
}
export async function setAutoUpdates(active, time) {
  return realFetch("/host/system/auto-updates", { method: "PUT", ...jsonBody({ active, time }) });
}
// The channel Hyperlite updates from on this node: stable (releases) or test (every merge into the test branch).
export async function fetchUpdateChannel() {
  return realFetch("/update/channel");
}
export async function setUpdateChannel(channel) {
  return realFetch("/update/channel", { method: "PUT", ...jsonBody({ channel }) });
}
export async function upgradeHostPackages(packages) {
  return realFetch("/host/system/updates/upgrade", { method: "POST", ...jsonBody({ packages }) });
}
// Reboot or power off this node: the host name typed back, running guests refused or shut down first.
export async function nodePower(payload) {
  return realFetch("/host/system/power", { method: "POST", ...jsonBody(payload) });
}
export async function fetchHostDns() {
  return realFetch("/host/system/dns");
}
export async function setHostDns(payload) {
  return realFetch("/host/system/dns", { method: "PUT", ...jsonBody(payload) });
}
export async function fetchHostTime() {
  return realFetch("/host/system/time");
}
export async function fetchHostTimezones() {
  return realList("/host/system/time/zones");
}
export async function setHostTime(payload) {
  return realFetch("/host/system/time", { method: "PUT", ...jsonBody(payload) });
}
// Rename this node: its host name and its /etc/hosts line (administrators). { nom, ancien }.
export async function renameLocalHost(name) {
  return realFetch("/host/system/hostname", { method: "PUT", ...jsonBody({ name: name }) });
}
export async function fetchHostSyslog() {
  return realFetch("/host/system/syslog");
}
export async function setHostSyslog(payload) {
  return realFetch("/host/system/syslog", { method: "PUT", ...jsonBody(payload) });
}
// This node's HTTPS certificate; every change restarts the service (`redemarrage`).
export async function fetchCertificate() {
  return realFetch("/host/certificate");
}
export async function importCertificate(pem) {
  return realFetch("/host/certificate", { method: "POST", ...jsonBody(pem) });
}
export async function requestAcmeCertificate(payload) {
  return realFetch("/host/certificate/acme", { method: "POST", ...jsonBody(payload) });
}
export async function restorePreviousCertificate() {
  return realFetch("/host/certificate/previous", { method: "POST" });
}
export async function selfSignedCertificate() {
  return realFetch("/host/certificate/self-signed", { method: "POST" });
}
export async function fetchHealth() {
  const res = await fetch("/health", { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status });
  return res.json();
}

function jsonBody(payload) {
  return { headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}

export function mountVMDriversIso(name, iso) {
  return realFetch(`/vms/${encodeURIComponent(name)}/cdrom`, { method: "PUT", ...jsonBody({ iso, target_dev: "hdd" }) });
}

export function ejectVMDriversIso(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/cdrom?target_dev=hdd`, { method: "DELETE" });
}

// Which VM firmwares this host can build: { uefi, uefi_secure, raison }.
export function fetchHostFirmware() {
  return realFetch("/host/firmware");
}

// Per-VM resource limits derived from the real host (GET /host/limits), which
// replace the 1-2 vCPU / 256-2048 MB bounds that were hard-coded in the forms.
export async function fetchHostLimits() {
  return realFetch("/host/limits");
}

export async function fetchDashboardSummary() {
  return realFetch("/dashboard");
}

// Live figures of a node recorded by the backend metrics collector (null until it has been sampled once).
function liveFields(l) {
  return {
    cpu_coeurs: l?.cores ?? null,
    cpu_modele: l?.cpu_model ?? null,
    cpu_utilisation: l?.cpu_pct ?? null,
    memoire_totale_mo: l?.mem_total_mb ?? null,
    used_memory_mb: l?.mem_used_mb ?? null,
    noyau: l?.kernel ?? null,
    os: l?.os ?? null,
    hypervisor_version: l?.hypervisor_version ?? null,
    libvirt_version: l?.libvirt_version ?? null,
    measured_at: l?.measured_at ?? null,
  };
}

export async function fetchNodes() {
  const d = await fetchDashboardSummary();
  const localNode = {
    // "local" is an internal SENTINEL identifier for "the host running this Hyperlite
    // instance", never a real machine name. The real name (d.hyperviseur.nom) is
    // displayed everywhere; only this `id` is used for internal comparisons.
    id: "local",
    name: d.hypervisor.name,
    state: d.hypervisor.connected ? "online" : "error",
    ...liveFields(d.live),
    available_memory_mb: d.available_memory_mb,
    stockage_total_go: d.storage.capacity_gb,
    stockage_utilise_go: d.storage.capacity_gb != null && d.storage.available_gb != null
      ? Math.round((d.storage.capacity_gb - d.storage.available_gb) * 100) / 100 : null,
    uptime_s: d.live?.uptime_s ?? d.hypervisor.uptime_s,
    ip: d.live?.address ?? null,
    version: `Hyperlite (${d.hypervisor.type})`,
    vms_running: d.vms.active,
    vms_stopped: d.vms.stopped,
    // Why this node changes nothing right now (a cluster node that lost the quorum), or null.
    read_only: d.read_only ?? null,
    read_only_cause: d.read_only_cause ?? null,
    read_only_waiting_for: d.read_only_waiting_for ?? [],
    // This node's database: "ok", "damaged" or "failing", with the repair that fits (app/core/db_health.py).
    database: d.database ?? null,
  };

  // Registered remote nodes. They used to be missing here: this function only ever
  // returned a single synthetic "local" node, so a remote node that was really
  // registered and working on the backend (GET /nodes) stayed invisible in the main
  // tree. It was a real bug reported when testing a real second physical node (only
  // the dedicated "Nodes" tab, which queries /nodes directly, showed it).
  let remoteNodes = [];
  try {
    const remotes = await fetchRemoteNodes();
    remoteNodes = await Promise.all(remotes.map(async (n) => {
      let s = null;
      try { s = await fetchRemoteNodeSummary(n.name); } catch { /* node unreachable for now: degrade instead of failing the whole dashboard */ }
      return {
        id: n.name,
        name: n.name,
        state: s ? (s.connected ? "online" : "error") : (n.status === "online" ? "online" : "error"),
        ...liveFields(n.live ?? s?.live),
        available_memory_mb: null,
        stockage_total_go: s?.storage_capacity_gb ?? null,
        stockage_utilise_go: s && s.storage_capacity_gb != null && s.storage_available_gb != null
          ? Math.round((s.storage_capacity_gb - s.storage_available_gb) * 100) / 100 : null,
        uptime_s: (n.live ?? s?.live)?.uptime_s ?? null,
        ip: n.hostname,
        ssh_port: n.ssh_port,
        ssh_user: n.ssh_user,
        version: "Hyperlite (remote)",
        vms_running: s?.vms_running ?? 0,
        vms_stopped: s?.vms_stopped ?? 0,
        distant: true,
        // A member of this node's cluster: it runs Hyperlite and keeps its VMs' backups (GET /nodes, "membre").
        member: Boolean(n.member),
      };
    }));
  } catch { /* GET /nodes unavailable: stay on the local node alone, as before this fix */ }

  // Maintenance state of every node ("local" for this host); a failure only hides the badge.
  let inMaintenance = {};
  try { inMaintenance = Object.fromEntries((await fetchNodeMaintenance()).map((m) => [m.node, m])); } catch { /* keep the nodes without it */ }
  return [localNode, ...remoteNodes].map((n) => ({ ...n, maintenance: inMaintenance[n.id] || null }));
}

// GET /vms does not return every statistic displayed by this dashboard yet
// (detailed disk, tags...): they are completed with default values until a richer
// GET /vms exists.
function mapVm(v, nodeId) {
  return {
    name: v.name, node: nodeId, type: "vm", state: v.state,
    vcpu: v.vcpu, memory_mb: v.memory_mb, used_memory_mb: null,
    disk_gb: null, disque_utilise_go: null,
    ip: v.ip, ssh_user: v.ssh_user, uuid: v.uuid,
    os: v.os, uptime_s: v.uptime_s,
    // This mapping whitelists the fields, so a field added on the backend
    // (stockage_zfs, see app/routers/vms.py::_domain_summary) is silently dropped
    // here unless it is listed: VMSnapshotsTab.jsx would always receive `undefined`.
    zfs_storage: v.zfs_storage,
    iscsi_storage: v.iscsi_storage,
    guest_agent: v.guest_agent,
    firmware: v.firmware,
  };
}

export async function fetchVMs() {
  // Every cluster member's VMs in one request, from the cluster's status (app/core/statd.py, phase E): this node asks
  // no other node on a refresh, whatever their number. Outside a cluster: this node's VMs.
  const [cluster, remotes] = await Promise.all([
    realFetch("/vms?cluster=true"),
    fetchRemoteNodes().catch(() => []), // GET /nodes unavailable: the members' VMs only
  ]);
  // A bare list is this node's VMs alone (the shape GET /vms always had).
  const items = Array.isArray(cluster) ? cluster : cluster.items || [];
  let result = items.map((v) => mapVm(v, v.node || "local"));

  // Nodes registered over SSH only (no Hyperlite on them, not members): still read one by one.
  const others = remotes.filter((n) => !n.member);
  const lists = await Promise.all(others.map(async (n) => {
    try {
      const vms = await realList(`/vms?node=${encodeURIComponent(n.name)}`);
      return vms.map((v) => mapVm(v, n.name));
    } catch { return []; /* node unreachable for now */ }
  }));
  return result.concat(...lists);
}

function mapPool(p, nodeId) {
  // `type` used to be hard-coded to "dir" here. That was harmless as long as
  // GET /storage never returned a real `type` field (all existing pools really were
  // "dir"), but it would have silently masked the new real field once NFS pools
  // existed on the backend.
  // protocol: "nfs" or "smb" for a netfs pool (null otherwise), with the version its mount is fixed to.
  return { name: p.name, node: nodeId, type: p.type, state: p.state, capacity_gb: p.capacity_gb, available_gb: p.available_gb, path: p.path ?? null,
    protocol: p.protocol ?? null, nfs_version: p.nfs_version ?? null, smb_version: p.smb_version ?? null };
}

export async function fetchStoragePools() {
  const localPools = await realList("/storage");
  let result = localPools.map((p) => mapPool(p, "local"));

  try {
    const remotes = await fetchRemoteNodes();
    const remoteLists = await Promise.all(remotes.map(async (n) => {
      try {
        const pools = await realList(`/storage?node=${encodeURIComponent(n.name)}`);
        return pools.map((p) => mapPool(p, n.name));
      } catch { return []; }
    }));
    result = result.concat(...remoteLists);
  } catch { /* GET /nodes unavailable */ }

  return result;
}

export async function fetchNetworks() {
  const nets = await realList("/networks");
  return nets.map((n) => ({ name: n.name, type: n.type, bridge: n.bridge, active: n.active, network: n.network, autostart: n.autostart, dhcp: n.dhcp, vms: n.vms, vlan: n.vlan === true, vlanMode: n.vlan_mode ?? null }));
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
// The host's interfaces a bridged network can sit on (existing bridges, NICs, bonds, VLANs; Wi-Fi listed as unusable).
export async function fetchHostInterfaces() {
  return realList("/networks/host-interfaces");
}
// Subnet, DHCP range and mode of a NAT/isolated network; `a_redemarrer` when the change waits for its restart.
export async function editNetwork(name, payload) {
  return realFetch(`/networks/${encodeURIComponent(name)}`, { method: "PATCH", ...jsonBody(payload) });
}
export async function addNetworkReservation(name, payload) {
  return realFetch(`/networks/${encodeURIComponent(name)}/reservations`, { method: "POST", ...jsonBody(payload) });
}
export async function deleteNetworkReservation(name, mac) {
  return realFetch(`/networks/${encodeURIComponent(name)}/reservations/${encodeURIComponent(mac)}`, { method: "DELETE" });
}
export async function startNetwork(name) {
  return realFetch(`/networks/${encodeURIComponent(name)}/start`, { method: "POST" });
}
export async function stopNetwork(name) {
  return realFetch(`/networks/${encodeURIComponent(name)}/stop?confirm=true`, { method: "POST" });
}
export async function setNetworkAutostart(name, autostart) {
  return realFetch(`/networks/${encodeURIComponent(name)}/autostart`, { method: "PUT", ...jsonBody({ autostart }) });
}

// ---- Network firewall (real: GET/PUT /networks/{name}/firewall), distinct from
// the per-VM firewall: it filters at the bridge level, not at the interface level ----
export async function fetchNetworkFirewall(name) {
  return realFetch(`/networks/${encodeURIComponent(name)}/firewall`);
}
export async function setNetworkFirewall(name, payload) {
  return realFetch(`/networks/${encodeURIComponent(name)}/firewall`, { method: "PUT", ...jsonBody(payload) });
}

// ---- Per-VM firewall (real: GET/PUT /vms/{name}/firewall, libvirt nwfilter) ----
export async function fetchVMFirewall(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/firewall`);
}
export async function setVMFirewall(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/firewall`, { method: "PUT", ...jsonBody(payload) });
}

// ---- Native backups (real: GET/POST /vms/{name}/backups, DELETE /backups/{id},
// POST /backups/{id}/restore, GET/PUT/DELETE /vms/{name}/backup-schedule; see
// app/routers/backups.py) ----
// In a cluster, each node keeps the backups of the VMs it hosts (app/routers/backups.py): a VM of another node is
// backed up and listed through `node` (its node, "local" or nothing for this one), a backup through `on` (the node
// that holds it, as GET /backups?cluster=true marks it). This node then sends the request to that node.
const backupQs = (key, value, extra = "") => {
  const parts = [extra, value && value !== "local" ? `${key}=${encodeURIComponent(value)}` : ""].filter(Boolean);
  return parts.length ? `?${parts.join("&")}` : "";
};
// { items, unreachable }: every member's backups, each with the node that holds it ("on").
export async function fetchAllBackups() {
  return realFetch("/backups?cluster=true");
}
export async function fetchVMBackups(name, node) {
  return realList(`/vms/${encodeURIComponent(name)}/backups${backupQs("node", node)}`);
}
export async function createBackup(name, targetDir = null, node) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backups${backupQs("node", node)}`, { method: "POST", ...jsonBody({ target_dir: targetDir }) });
}
export async function deleteBackup(id, on) {
  return realFetch(`/backups/${id}${backupQs("on", on, "confirm=true")}`, { method: "DELETE" });
}
export async function restoreBackup(id, mode, newName = null, on) {
  return realFetch(`/backups/${id}/restore${backupQs("on", on)}`, { method: "POST", ...jsonBody({ mode, new_name: newName }) });
}
// Recompute every checksum of a backup and check its images (a task, held by the same node as the backup).
export async function verifyBackup(id, on) {
  return realFetch(`/backups/${id}/verify${backupQs("on", on)}`, { method: "POST" });
}
// Recovery of a lost site (app/core/site_recovery.py): the other site's backups found in a directory of this node,
// then restored here as new VMs.
export async function scanSiteBackups(path) {
  return realList(`/backups/site-recovery/scan?path=${encodeURIComponent(path)}`);
}
export async function startSiteRecovery(elements, network) {
  return realFetch("/backups/site-recovery", { method: "POST", ...jsonBody({ elements, network: network || null }) });
}
// Replication to another site (app/core/replication.py): jobs and each VM's last copy.
export async function fetchReplicationJobs() {
  return realList("/replication/jobs");
}
export async function createReplicationJob(payload) {
  return realFetch("/replication/jobs", { method: "POST", ...jsonBody(payload) });
}
export async function updateReplicationJob(id, payload) {
  return realFetch(`/replication/jobs/${id}`, { method: "PUT", ...jsonBody(payload) });
}
export async function deleteReplicationJob(id) {
  return realFetch(`/replication/jobs/${id}`, { method: "DELETE" });
}
export async function runReplicationJob(id) {
  return realFetch(`/replication/jobs/${id}/run`, { method: "POST" });
}
export async function fetchReplicationStatus() {
  return realList("/replication/status");
}
export async function fetchBackupSchedule(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backup-schedule`);
}
export async function setBackupSchedule(name, payload, node) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backup-schedule${backupQs("node", node)}`, { method: "PUT", ...jsonBody(payload) });
}
export async function deleteBackupSchedule(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/backup-schedule`, { method: "DELETE" });
}

export async function fetchIsoTemplates() {
  return realList("/isos");
}
export async function deleteIso(filename, node = "local") {
  return realFetch(`/isos/${encodeURIComponent(filename)}?confirm=true&node=${encodeURIComponent(node)}`, { method: "DELETE" });
}
// Every node's ISO library ({ isos, injoignables }), and copying one image to other nodes.
export async function fetchClusterIsos() {
  return realFetch("/isos/cluster");
}
export async function copyIso(name, source, targets) {
  return realFetch("/isos/copy", { method: "POST", ...jsonBody({ name, source, targets }) });
}

// ---- Kubernetes (k3s) clusters on VMs ----
export async function fetchK8sClusters() {
  return realList("/kubernetes/clusters");
}
export async function createK8sCluster(payload) {
  return realFetch("/kubernetes/clusters", { method: "POST", ...jsonBody(payload) });
}
export async function deleteK8sCluster(name) {
  return realFetch(`/kubernetes/clusters/${encodeURIComponent(name)}?confirm=true`, { method: "DELETE" });
}
// The kubeconfig is YAML, not JSON: read it as text, with the same authentication as realFetch.
export async function fetchKubeconfig(name) {
  const res = await fetch(apiPath(`/kubernetes/clusters/${encodeURIComponent(name)}/kubeconfig`), { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json()).detail; } catch { /* not JSON: keep the generic message */ }
    // Same session handling as every other call: an expired session signs out instead of looking like an error.
    if (res.status === 401 && token) unauthorizedHandler?.();
    throw Object.assign(new Error(normalizeDetail(detail) || "Unknown error"), { status: res.status });
  }
  return res.text();
}

// ---- Importable disks (importing a VM from a disk file) ----
export async function fetchVmDisks() {
  return realList("/vm-disks");
}
export async function deleteVmDisk(filename) {
  return realFetch(`/vm-disks/${encodeURIComponent(filename)}`, { method: "DELETE" });
}

// ---- VM export ----
export async function fetchVmExports() {
  return realList("/vm-exports");
}
export async function exportVM(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/export`, { method: "POST" });
}
export async function deleteVmExport(filename) {
  return realFetch(`/vm-exports/${encodeURIComponent(filename)}`, { method: "DELETE" });
}
export async function downloadVmExport(filename) {
  const { ticket } = await realFetch(`/vm-exports/${encodeURIComponent(filename)}/download-ticket`, { method: "POST" });
  window.open(apiPath(`/vm-exports/download?ticket=${encodeURIComponent(ticket)}`), "_blank");
}

// ---- Audit journal (real: the audit_log table, fed by every action) ----
// Complete CSV exports (GET /audit/export.csv, /tasks/export.csv): every row matching the
// filters, streamed by the server, not only the rows loaded on the page.
async function downloadCsv(path, filters, fallbackName) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, v);
  });
  const qs = params.toString();
  let res;
  try {
    res = await fetch(apiPath(`${path}${qs ? `?${qs}` : ""}`), { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  } catch {
    throw new Error("Cannot reach the server. Check your network connection and try again.");
  }
  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json()).detail; } catch { /* not JSON */ }
    throw Object.assign(new Error(normalizeDetail(detail) || `HTTP ${res.status}`), { status: res.status });
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
// File-level restore: a browsing session on a backup's disks, its folders, and a file (or a folder as .tar.gz).
export async function openBackupFiles(backupId, on) {
  return realFetch(`/backups/${backupId}/files${backupQs("on", on)}`, { method: "POST" });
}
export async function listBackupDir(session, device, path) {
  return realFetch(`/file-restore/${encodeURIComponent(session)}/ls?${new URLSearchParams({ device, path })}`);
}
export function downloadBackupFile(session, device, path) {
  return downloadCsv(`/file-restore/${encodeURIComponent(session)}/download`, { device, path }, "restored-file");
}
export async function closeBackupFiles(session) {
  return realFetch(`/file-restore/${encodeURIComponent(session)}`, { method: "DELETE" });
}
export function downloadAuditCsv(filters = {}) {
  return downloadCsv("/audit/export.csv", filters, "hyperlite-audit.csv");
}
export function downloadTasksCsv(filters = {}) {
  return downloadCsv("/tasks/export.csv", filters, "hyperlite-tasks.csv");
}

// The activity of a cluster (tasks and audit log, app/core/cluster_view.py) is asked of every member: cluster=true.
const clusterQuery = (filters) => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, v);
  });
  params.set("cluster", "true");
  return params.toString();
};
const onNode = (on) => (on ? `?on=${encodeURIComponent(on)}` : "");

// The audit log of every member in a cluster: { items, unreachable }, as the tasks below.
export async function fetchAuditLog(filters = {}) {
  return realFetch(`/audit?${clusterQuery(filters)}`);
}
export async function fetchAuditActions() {
  return realList("/audit/actions?cluster=true");
}

// ---- Automation: the job engine (real: /jobs, see app/routers/jobs.py) ----
export async function fetchJobs() {
  return realList("/jobs");
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
  return realList(`/jobs/${id}/runs`);
}
export async function fetchJobRun(runId) {
  return realFetch(`/jobs/runs/${runId}`);
}

// ---- Multi-node (real: /nodes, see app/routers/nodes.py) ----
export async function fetchRemoteNodes() {
  return realList("/nodes");
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
// The name Hyperlite shows for a registered node (its host name and address do not change).
export async function renameRemoteNode(name, newName) {
  return realFetch(`/nodes/${encodeURIComponent(name)}/rename`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
export async function deleteRemoteNode(name) {
  return realFetch(`/nodes/${encodeURIComponent(name)}`, { method: "DELETE" });
}

// ---- Persisted tasks (real: the tasks table, with creation/start/end
// timestamps; see app/core/tasks.py). Replaces the theoretical fetchTasks() that
// was closed over in NodeTasksTab.jsx with a real, filterable and sortable
// GET /tasks. ----
// In a cluster, the tasks of every member (app/core/cluster_view.py): { items, unreachable }, each item marked with
// the node that holds it ("on"), which its detail, log and cancellation are sent to. Outside a cluster, this node's.
export async function fetchClusterTasks(filters = {}) {
  return realFetch(`/tasks?${clusterQuery(filters)}`);
}
export async function fetchTasks(filters = {}) {
  const r = await fetchClusterTasks(filters);
  return Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : [];
}
export async function fetchTaskDetail(id, on) {
  return realFetch(`/tasks/${encodeURIComponent(id)}${onNode(on)}`);
}
// A task's own log, and asking it to stop (force: close the record of one that cannot stop, administrator only).
export async function fetchTaskLog(id, on) {
  return realList(`/tasks/${encodeURIComponent(id)}/log${onNode(on)}`);
}
export async function cancelTask(id, force = false, on) {
  const query = [force ? "force=true" : "", on ? `on=${encodeURIComponent(on)}` : ""].filter(Boolean).join("&");
  return realFetch(`/tasks/${encodeURIComponent(id)}/cancel${query ? `?${query}` : ""}`, { method: "POST" });
}

// ---- Hyperlite update from Git (real: GET/POST /update/*, see
// app/routers/update.py) ----
export async function fetchUpdateCheck(lang = "en") {
  // The node reads its repositories, retrying a slow mirror (up to about 2 minutes): past the usual read limit.
  return realFetch(`/update/check?lang=${encodeURIComponent(lang)}`, { signal: AbortSignal.timeout(180000) });
}
export async function rollbackUpdate() {
  return realFetch("/update/rollback", { method: "POST" });
}
// Update from a file, for a node without network: the file published next to the ISO. The upload goes through
// XMLHttpRequest for its progress event (hundreds of megabytes).
export function uploadUpdateFile(file, lang, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiPath(`/update/offline?lang=${encodeURIComponent(lang)}`));
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.addEventListener("progress", (ev) => { if (ev.lengthComputable) onProgress?.(Math.round((ev.loaded / ev.total) * 100)); });
    xhr.addEventListener("load", () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body?.detail || `HTTP error ${xhr.status}`));
    });
    xhr.addEventListener("error", () => reject(new Error("Network error during upload")));
    xhr.send(fd);
  });
}
export async function fetchUpdateFile(lang = "en") {
  return realFetch(`/update/offline?lang=${encodeURIComponent(lang)}`);
}
export async function deleteUpdateFile() {
  return realFetch("/update/offline", { method: "DELETE" });
}
export async function applyUpdateFile(system = true) {
  return realFetch("/update/offline/apply", { method: "POST", ...jsonBody({ system }) });
}
export async function applyUpdate(system = true) {
  return realFetch("/update/apply", { method: "POST", ...jsonBody({ system }) });
}

// ---- Users (real) ----
export async function fetchUsers() {
  return realList("/auth/users");
}
export async function createUser(username, password, role) {
  return realFetch("/auth/users", { method: "POST", ...jsonBody({ username, password, role }) });
}
// The signed-in user changes their own password (current one + 2FA code when enabled); returns a fresh
// session token, every other session of the account being signed out by the server.
export async function changeMyPassword(currentPassword, newPassword, code) {
  return realFetch("/auth/me/password", { method: "POST", ...jsonBody({ current_password: currentPassword, new_password: newPassword, code: code || null }) });
}

// An administrator sets a new password for another account; its sessions and tokens are revoked.
export async function resetUserPassword(username, password) {
  return updateUser(username, { password });
}

export async function updateUser(username, payload) {
  return realFetch(`/auth/users/${encodeURIComponent(username)}`, { method: "PATCH", ...jsonBody(payload) });
}
export async function deleteUser(username) {
  return realFetch(`/auth/users/${encodeURIComponent(username)}`, { method: "DELETE" });
}

// ---- VM actions (real endpoints) ----
// node: "local" or omitted = the local host (the historical behaviour,
// unchanged), otherwise the name of a registered remote node. The same
// convention as fetchVMs()/migrateVM().
export async function startVM(name, node = null) {
  const q = node && node !== "local" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/vms/${encodeURIComponent(name)}/start${q}`, { method: "POST" });
}
export async function stopVM(name, force = false, node = null, forceAfter = null) {
  const params = new URLSearchParams({ force: String(force) });
  if (forceAfter) params.set("force_after", String(forceAfter));
  if (node && node !== "local") params.set("node", node);
  return realFetch(`/vms/${encodeURIComponent(name)}/stop?${params}`, { method: "POST" });
}
export async function restartVM(name, node = null) {
  const params = new URLSearchParams({ force: "true" });
  if (node && node !== "local") params.set("node", node);
  return realFetch(`/vms/${encodeURIComponent(name)}/restart?${params}`, { method: "POST" });
}
export async function deleteVM(name, node = null) {
  const params = new URLSearchParams({ confirm: "true" });
  if (node && node !== "local") params.set("node", node);
  return realFetch(`/vms/${encodeURIComponent(name)}?${params}`, { method: "DELETE" });
}
export async function cloneVM(name, newName) {
  return realFetch(`/vms/${encodeURIComponent(name)}/clone`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
// Live migration: sourceNode "local" (or omitted) = the local host, the same
// convention as the rest (open_conn(node), fetchVMs...).
export async function migrateVM(name, targetNode, sourceNode, ignorerVerifications = false) {
  const qs = sourceNode && sourceNode !== "local" ? `?node=${encodeURIComponent(sourceNode)}` : "";
  return realFetch(`/vms/${encodeURIComponent(name)}/migrate${qs}`, { method: "POST", ...jsonBody({ target_node: targetNode, skip_checks: ignorerVerifications }) });
}
// ---- Node maintenance (node "local" = this host). Draining live-migrates the running VMs to targetNode;
// targetNode null only marks the node.
export async function fetchNodeMaintenance() {
  return realList("/nodes/maintenance");
}
export async function fetchDrainPlan(node, targetNode) {
  const qs = targetNode ? `?target_node=${encodeURIComponent(targetNode)}` : "";
  return realFetch(`/nodes/${encodeURIComponent(node)}/drain-plan${qs}`);
}
export async function enterNodeMaintenance(node, targetNode) {
  return realFetch(`/nodes/${encodeURIComponent(node)}/maintenance`, { method: "POST", ...jsonBody({ target_node: targetNode || null }) });
}
export async function leaveNodeMaintenance(node) {
  return realFetch(`/nodes/${encodeURIComponent(node)}/maintenance`, { method: "DELETE" });
}
// Cluster compatibility diagnostic
export async function fetchMigrationCheck(name, targetNode, sourceNode) {
  const params = new URLSearchParams({ target_node: targetNode });
  if (sourceNode && sourceNode !== "local") params.set("node", sourceNode);
  return realFetch(`/vms/${encodeURIComponent(name)}/migration-check?${params}`);
}
export async function fetchNodeCompatibility(nodeName) {
  return realFetch(`/nodes/${encodeURIComponent(nodeName)}/compatibility`);
}
// Copy of the cluster configuration to the nodes (app/core/config_copy.py).
export function fetchConfigCopies() {
  return realFetch("/nodes/config-copy");
}
export function copyConfigNow() {
  return realFetch("/nodes/config-copy", { method: "POST" });
}
// HA: see app/core/ha.py. Recovery is always triggered by an admin; the watcher (ha_watch.py) runs in dry-run mode
// and only records what automatic HA would have done. Fencing settings are tested with a status query only.
export function fetchHaStatus() {
  return realFetch("/ha/status");
}
export function saveHaSettings(payload) {
  return realFetch("/ha/settings", { method: "PUT", ...jsonBody(payload) });
}
export function fetchFencing() {
  return realList("/ha/fencing");
}
export function saveFencing(node, payload) {
  return realFetch(`/ha/fencing/${encodeURIComponent(node)}`, { method: "PUT", ...jsonBody(payload) });
}
export function deleteFencing(node) {
  return realFetch(`/ha/fencing/${encodeURIComponent(node)}`, { method: "DELETE" });
}
export function testFencing(node) {
  return realFetch(`/ha/fencing/${encodeURIComponent(node)}/test`, { method: "POST" });
}
export function checkLeases(node) {
  return realFetch(`/ha/leases/${encodeURIComponent(node)}`);
}
export async function fetchHaProtected() {
  return realList("/ha");
}
export async function enableHa(name, node) {
  return realFetch(`/ha/${encodeURIComponent(name)}/enable`, { method: "POST", ...jsonBody({ node: node && node !== "local" ? node : null }) });
}
export async function disableHa(name) {
  return realFetch(`/ha/${encodeURIComponent(name)}`, { method: "DELETE" });
}
export async function recoverHa(name, targetNode) {
  return realFetch(`/ha/${encodeURIComponent(name)}/recover`, { method: "POST", ...jsonBody({ target_node: targetNode }) });
}

// Automatic deletion of inactive VMs (opt-in per VM, see app/core/vm_cleanup.py).
// The counter only runs while the VM is stopped, and never if it is HA-protected.
export async function fetchVMAutoCleanup(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/auto-cleanup`);
}
export async function setVMAutoCleanup(name, inactiveDays) {
  return realFetch(`/vms/${encodeURIComponent(name)}/auto-cleanup`, { method: "PUT", ...jsonBody({ inactive_days: inactiveDays }) });
}
export async function disableVMAutoCleanup(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/auto-cleanup`, { method: "DELETE" });
}
// Outgoing notifications: see app/core/notifications.py.
export async function fetchNotifyEvents() {
  return realFetch("/notifications/events");
}
export async function fetchNotificationChannels() {
  return realList("/notifications/channels");
}
export async function createNotificationChannel(payload) {
  return realFetch("/notifications/channels", { method: "POST", ...jsonBody(payload) });
}
// Only the fields given are changed; an empty smtp_password keeps the stored one.
export async function updateNotificationChannel(id, patch) {
  return realFetch(`/notifications/channels/${id}`, { method: "PATCH", ...jsonBody(patch) });
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

// OIDC SSO: see app/core/sso.py / app/routers/sso.py. fetchSsoStatus() is called
// WITHOUT a token (login screen, nobody is authenticated yet); realFetch only adds
// the Authorization header when a token is present, so it can be reused as is here.
export async function fetchSsoStatus() {
  return realFetch("/auth/sso/status");
}
// LDAP / Active Directory sign-in (admin).
export async function fetchLdapConfig() {
  return realFetch("/ldap/config");
}
export async function saveLdapConfig(payload) {
  return realFetch("/ldap/config", { method: "PUT", ...jsonBody(payload) });
}
export async function testLdap(payload) {
  return realFetch("/ldap/test", { method: "POST", ...jsonBody(payload) });
}
export async function fetchSsoConfig() {
  return realFetch("/auth/sso/config");
}
export async function updateSsoConfig(payload) {
  return realFetch("/auth/sso/config", { method: "PUT", ...jsonBody(payload) });
}
// Tests the saved issuer: the server only contacts the stored configuration, never a URL sent by the page.
export async function testSso() {
  return realFetch("/auth/sso/test", { method: "POST" });
}

export async function createVM(payload) {
  return realFetch("/vms", { method: "POST", ...jsonBody(payload) });
}
export async function updateVM(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}`, { method: "PATCH", ...jsonBody(payload) });
}
// node: the node the VM runs on; omitted (or "local") for the local host.
const nodeQuery = (node) => (node && node !== "local" ? `?node=${encodeURIComponent(node)}` : "");
export async function fetchVM(name, node = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}${nodeQuery(node)}`);
}
// A stopped VM without snapshots takes a new name; its settings, backups and history follow it (administrator).
export async function renameVM(name, newName, node = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/rename${nodeQuery(node)}`, { method: "POST", ...jsonBody({ new_name: newName }) });
}

// ---- Notes and tags of VMs, containers and nodes (GET /meta, GET/PUT /meta/{kind}/{name}); a VM's node travels
// with the call, a node is identified by its own name.
export async function fetchMetaList(kind = null) {
  return realList(`/meta${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`);
}
export async function fetchMeta(kind, name, node = null) {
  const q = node && node !== "local" && kind === "vm" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/meta/${kind}/${encodeURIComponent(name)}${q}`);
}
export async function saveMeta(kind, name, payload, node = null) {
  const q = node && node !== "local" && kind === "vm" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/meta/${kind}/${encodeURIComponent(name)}${q}`, { method: "PUT", ...jsonBody(payload) });
}
// ---- Advanced hardware settings of a VM of this host: disk options, boot order, ballooning, machine type.
export async function fetchVMHardwareOptions(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/hardware-options`);
}
export async function setVMDiskOptions(name, dev, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks/${encodeURIComponent(dev)}/options`, { method: "PUT", ...jsonBody(payload) });
}
export async function setVMBootOrder(name, position) {
  return realFetch(`/vms/${encodeURIComponent(name)}/boot-order`, { method: "PUT", ...jsonBody({ position }) });
}
export async function setVMBalloon(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/balloon`, { method: "PUT", ...jsonBody(payload) });
}
export async function setVMMachine(name, machine) {
  return realFetch(`/vms/${encodeURIComponent(name)}/machine`, { method: "PUT", ...jsonBody({ machine }) });
}
// ---- Cloud-init after creation (GET/PUT /vms/{name}/cloud-init, VMs of this host made from a cloud image).
export async function fetchVMCloudInit(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/cloud-init`);
}
export async function setVMCloudInit(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/cloud-init`, { method: "PUT", ...jsonBody(payload) });
}
// ---- Start at boot (GET/PUT /vms/{name}/boot): per node, so the VM's node travels with the call.
// Settings a running VM takes only at its next start (live definition vs saved one).
export async function fetchVMPendingChanges(name, node = null) {
  const q = node && node !== "local" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/vms/${encodeURIComponent(name)}/pending-changes${q}`);
}
export async function fetchVMBoot(name, node = null) {
  const q = node && node !== "local" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/vms/${encodeURIComponent(name)}/boot${q}`);
}
export async function setVMBoot(name, payload, node = null) {
  const q = node && node !== "local" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/vms/${encodeURIComponent(name)}/boot${q}`, { method: "PUT", ...jsonBody(payload) });
}
// ---- Resource limits/reservations (real: GET/PUT /vms/{name}/limits, cgroups
// through libvirt schedulerParametersFlags/memoryParameters) ----
export async function fetchVMLimits(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/limits`);
}
export async function setVMLimits(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/limits`, { method: "PUT", ...jsonBody(payload) });
}
// CPU pinning and NUMA placement (GET/PUT /vms/{name}/cpu-pinning), with the host topology.
export async function fetchVMCpuPinning(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/cpu-pinning`);
}
export async function setVMCpuPinning(name, payload) {
  return realFetch(`/vms/${encodeURIComponent(name)}/cpu-pinning`, { method: "PUT", ...jsonBody(payload) });
}
// Host devices (PCI and USB passthrough): the host inventory and a VM's devices.
export function fetchHostDevices() {
  return realFetch("/host/devices");
}
export function fetchVMHostDevices(name) {
  return realList(`/vms/${encodeURIComponent(name)}/hostdevs`);
}
export function attachVMHostDevice(name, device, confirm = false) {
  return realFetch(`/vms/${encodeURIComponent(name)}/hostdevs`, { method: "POST", ...jsonBody({ device, confirm }) });
}
export function detachVMHostDevice(name, device) {
  return realFetch(`/vms/${encodeURIComponent(name)}/hostdevs/${encodeURIComponent(device)}`, { method: "DELETE" });
}
export async function fetchVMMetricsHistory(name, range = "1h", node = null) {
  const params = new URLSearchParams({ range });
  if (node && node !== "local") params.set("node", node);
  return realList(`/vms/${encodeURIComponent(name)}/metrics/history?${params}`);
}
// History of a node: "local" = this host, otherwise a registered remote node.
export async function fetchNodeMetricsHistory(node, range = "1h") {
  return realList(`/nodes/${encodeURIComponent(node)}/metrics/history?range=${encodeURIComponent(range)}`);
}
export async function fetchStorageHistory(range = "24h", node = null) {
  const params = new URLSearchParams({ range });
  if (node) params.set("node", node);
  return realList(`/storage/history?${params}`);
}
export async function fetchNodeHardware(node) {
  return realFetch(`/nodes/${encodeURIComponent(node)}/hardware`);
}
export async function testNodeConnection(payload) {
  return realFetch("/nodes/test", { method: "POST", ...jsonBody(payload) });
}
// Grouped backup jobs (admin): all VMs of this node, a tag's or a pool's, on one schedule.
export async function fetchBackupGroups() {
  return realList("/backup-groups");
}
export async function createBackupGroup(payload) {
  return realFetch("/backup-groups", { method: "POST", ...jsonBody(payload) });
}
export async function updateBackupGroup(id, payload) {
  return realFetch(`/backup-groups/${id}`, { method: "PUT", ...jsonBody(payload) });
}
export async function deleteBackupGroup(id) {
  return realFetch(`/backup-groups/${id}`, { method: "DELETE" });
}
export async function runBackupGroup(id) {
  return realFetch(`/backup-groups/${id}/run`, { method: "POST" });
}
export async function fetchBackupSchedules() {
  return realList("/backup-schedules");
}
// How long the users' addresses are kept: the access log's daily files and the audit log (0: none / for ever).
export function fetchRetention() {
  return realFetch("/audit/retention");
}
export function saveRetention(payload) {
  return realFetch("/audit/retention", { method: "PUT", ...jsonBody(payload) });
}
export async function fetchAuditCount(filters = {}) {
  return realFetch(`/audit/count?${clusterQuery(filters)}`);
}
const nodeQs = (node) => (node && node !== "local" ? `?node=${encodeURIComponent(node)}` : "");
export async function fetchVMDisks(name, node = null) {
  return realList(`/vms/${encodeURIComponent(name)}/disks${nodeQs(node)}`);
}
export async function attachDisk(name, volumeName, targetDev, pool = "default") {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks`, { method: "POST", ...jsonBody({ volume_name: volumeName, pool, target_dev: targetDev }) });
}
export async function detachDisk(name, targetDev) {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks/${encodeURIComponent(targetDev)}`, { method: "DELETE" });
}
// The new TOTAL size in GB (grow only; the backend refuses a shrink).
// Move a disk to another directory/NFS pool (a background task; the source is kept unless deleteSource).
export async function moveDisk(name, targetDev, pool, deleteSource = false) {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks/${encodeURIComponent(targetDev)}/move`, { method: "POST", ...jsonBody({ pool, delete_source: deleteSource }) });
}
export async function resizeDisk(name, targetDev, sizeGb) {
  return realFetch(`/vms/${encodeURIComponent(name)}/disks/${encodeURIComponent(targetDev)}/resize`, { method: "POST", ...jsonBody({ size_gb: sizeGb }) });
}
export async function fetchVMNetwork(name, node = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/network${nodeQs(node)}`);
}
export async function attachInterface(name, network, vlanTag = null, vlanTrunks = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/interfaces`, { method: "POST", ...jsonBody({ network, vlan_tag: vlanTag, vlan_trunks: vlanTrunks }) });
}
export async function detachInterface(name, mac) {
  return realFetch(`/vms/${encodeURIComponent(name)}/interfaces/${encodeURIComponent(mac)}`, { method: "DELETE" });
}
export async function createVolume(pool, name, sizeGb) {
  return realFetch(`/storage/${encodeURIComponent(pool)}/volumes`, { method: "POST", ...jsonBody({ name, size_gb: sizeGb }) });
}
// Irreversible: the server refuses a volume a VM uses.
export async function deleteVolume(pool, name) {
  return realFetch(`/storage/${encodeURIComponent(pool)}/volumes/${encodeURIComponent(name)}?confirm=true`, { method: "DELETE" });
}
export async function fetchVolumes(pool) {
  return realList(`/storage/${encodeURIComponent(pool)}/volumes`);
}
// Shared storage: node is optional, the same convention as the rest (fetchVMs,
// fetchStoragePools...): it creates/deletes a pool on a registered remote node
// instead of the local host.
// What the local host can create now (NFS client, ZFS module): the pool form warns before trying.
export async function fetchStorageSupport() {
  return realFetch("/storage/support");
}
// Whether QEMU can own its disk files on an NFS pool of this host (root_squash): { ok, message }.
export async function checkPoolPermissions(name) {
  return realFetch(`/storage/${encodeURIComponent(name)}/check-permissions`, { method: "POST" });
}
export async function createStoragePool(payload, node) {
  const qs = node ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/storage${qs}`, { method: "POST", ...jsonBody(payload) });
}
// Storage declared for several nodes (NFS, iSCSI): which pools, and on which nodes.
export async function fetchSharedPools() {
  return realList("/storage/shared");
}
// everywhere: a shared pool is removed from every node that has it, with its definition.
export async function deleteStoragePool(poolName, node, detach = false, everywhere = false) {
  const params = new URLSearchParams({ confirm: "true" });
  if (detach) params.set("detach", "true");
  if (everywhere) params.set("everywhere", "true");
  if (node) params.set("node", node);
  return realFetch(`/storage/${encodeURIComponent(poolName)}?${params.toString()}`, { method: "DELETE" });
}

// ---- VNC console / SSH terminal (real WebSocket relays) ----
export async function createConsoleTicket(name, node = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/console-ticket${nodeQuery(node)}`, { method: "POST" });
}
export async function createTerminalTicket(name, node = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/terminal-ticket${nodeQuery(node)}`, { method: "POST" });
}
// The VM's serial console (`virsh console` on its node): what the guest prints on its serial port.
export async function createSerialTicket(name, node = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/serial-ticket${nodeQuery(node)}`, { method: "POST" });
}
// SPICE (docs/design/spice-console.md): the .vv file virt-viewer opens, through Hyperlite's SPICE proxy. It holds a
// password valid a few seconds: saved straight to a file, never kept in the page.
export async function downloadSpiceFile(name, node = null) {
  let res;
  try {
    res = await fetch(apiPath(`/vms/${encodeURIComponent(name)}/spice-ticket${nodeQuery(node)}`), { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {} });
  } catch {
    throw new Error("Cannot reach the server. Check your network connection and try again.");
  }
  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json()).detail; } catch { /* not JSON */ }
    throw Object.assign(new Error(normalizeDetail(detail) || `HTTP ${res.status}`), { status: res.status });
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = `${name}.vv`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
export async function addSpiceDisplay(name, node = null) {
  return realFetch(`/vms/${encodeURIComponent(name)}/spice-display${nodeQuery(node)}`, { method: "POST" });
}

// ---- Interactive shell on the physical host (admin only, see app/routers/host.py) ----
export async function createHostTerminalTicket() {
  return realFetch("/host/terminal-ticket", { method: "POST" });
}

// ---- Snapshots (real) ----
export async function fetchSnapshots(name) {
  return realList(`/vms/${encodeURIComponent(name)}/snapshots`);
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

// ---- Templates (real) ----
export async function fetchTemplates() {
  return realList("/templates");
}
// keepVm: the template gets a copy of the VM's disk and the VM stays (vSphere's "Clone to Template"); without it the
// VM becomes the template and leaves the list (Proxmox VE's "Convert to template").
export async function createTemplateFromVM(vmName, templateName, keepVm = false) {
  return realFetch(`/templates/from-vm/${encodeURIComponent(vmName)}`, { method: "POST", ...jsonBody({ template_name: templateName || null, keep_vm: !!keepVm }) });
}
// linked: the new VM's disk reads through the template's (a linked clone) instead of being a full copy.
export async function deployTemplate(templateName, newName, network, linked = false) {
  return realFetch(`/templates/${encodeURIComponent(templateName)}/deploy`, { method: "POST", ...jsonBody({ new_name: newName, network: network || null, linked }) });
}
export async function deleteTemplate(templateName) {
  return realFetch(`/templates/${encodeURIComponent(templateName)}?confirm=true`, { method: "DELETE" });
}

// ---- Granular permissions (real): groups, pools, ACL ----
export async function fetchGroups() {
  return realList("/groups");
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
  return realList("/pools");
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
  return realList("/acl");
}
export async function createAcl(payload) {
  return realFetch("/acl", { method: "POST", ...jsonBody(payload) });
}
// Assignments on one VM or container, with those a VM inherits from its pools (`herite_de`).
export async function fetchObjectAcl(kind, name) {
  return realList(`/acl/object/${kind}/${encodeURIComponent(name)}`);
}
export async function deleteAcl(aclId) {
  return realFetch(`/acl/${aclId}`, { method: "DELETE" });
}

export async function fetchPrivileges() {
  return realFetch("/acl/privileges"); // a catalogue {privilege: label}
}
export async function fetchCustomRoles() {
  return realList("/acl/custom-roles");
}
export async function createCustomRole(name, privileges) {
  return realFetch("/acl/custom-roles", { method: "POST", ...jsonBody({ name, privileges }) });
}
export async function deleteCustomRole(roleId) {
  return realFetch(`/acl/custom-roles/${roleId}`, { method: "DELETE" });
}

// ---- LXC containers (real: GET/POST/DELETE /containers) ----
export async function fetchContainers() {
  return realList("/containers");
}
// One container's details (interfaces, DNS servers, start at boot) and changing them (administrator).
export async function fetchContainer(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}`);
}
export async function updateContainer(name, payload) {
  return realFetch(`/containers/${encodeURIComponent(name)}`, { method: "PATCH", ...jsonBody(payload) });
}
export async function searchDockerHub(query) {
  return realList(`/containers/docker-hub/search?q=${encodeURIComponent(query)}`);
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
  return realFetch(`/containers/${encodeURIComponent(name)}?confirm=true`, { method: "DELETE" });
}
// What a Docker (application) container's process printed: { actif, disponible, lignes }.
export async function fetchContainerLogs(name, lines = 300) {
  return realFetch(`/containers/${encodeURIComponent(name)}/logs?lines=${lines}`);
}
export async function renameContainer(name, newName) {
  return realFetch(`/containers/${encodeURIComponent(name)}/rename`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
// Root shell inside a running container, Docker or LXC (administrators): like `docker exec -it … sh`.
export async function createContainerShellTicket(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}/shell-ticket`, { method: "POST" });
}
// Variables a well-known image needs to start (postgres: POSTGRES_PASSWORD) and useful ones.
export async function fetchImageEnv(image) {
  return realFetch(`/containers/image-env?image=${encodeURIComponent(image)}`);
}
export async function createContainerTerminalTicket(name) {
  return realFetch(`/containers/${encodeURIComponent(name)}/terminal-ticket`, { method: "POST" });
}

// Container clone and backup/restore. libvirt's LXC driver has no snapshot: a container snapshot is a copy of its
// directory, taken while it is stopped (app/core/container_snapshots.py).
export async function cloneContainer(name, newName) {
  return realFetch(`/containers/${encodeURIComponent(name)}/clone`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
export async function fetchContainerBackups() {
  return realList("/containers/backups");
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
export async function fetchContainerSnapshots(name) {
  return realList(`/containers/${encodeURIComponent(name)}/snapshots`);
}
export async function createContainerSnapshot(name, snapshot, description = null) {
  return realFetch(`/containers/${encodeURIComponent(name)}/snapshots`, { method: "POST", ...jsonBody({ name: snapshot, description }) });
}
export async function restoreContainerSnapshot(name, snapshot) {
  return realFetch(`/containers/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(snapshot)}/restore`, { method: "POST" });
}
export async function deleteContainerSnapshot(name, snapshot) {
  return realFetch(`/containers/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(snapshot)}`, { method: "DELETE" });
}

// Two-factor authentication and API tokens, self-service: each user manages their
// own account (no need to be an admin).
// Enrolling a second factor takes the password (as removing one does).
export async function setup2FA(password) {
  return realFetch("/auth/2fa/setup", { method: "POST", ...jsonBody({ password: password || "" }) });
}
export async function confirm2FA(code) {
  return realFetch("/auth/2fa/confirm", { method: "POST", ...jsonBody({ code }) });
}
export async function disable2FA(password, code) {
  return realFetch("/auth/2fa/disable", { method: "POST", ...jsonBody({ password, code }) });
}
// Security keys (WebAuthn), self-service: list, registration challenge, registration, removal (password).
export function fetchSecurityKeys() {
  return realList("/auth/webauthn/keys");
}
export function securityKeyOptions(password) {
  return realFetch("/auth/webauthn/keys/options", { method: "POST", ...jsonBody({ password: password || "" }) });
}
export function registerSecurityKey(credential, name) {
  return realFetch("/auth/webauthn/keys", { method: "POST", ...jsonBody({ credential, name }) });
}
export function deleteSecurityKey(id, password) {
  return realFetch(`/auth/webauthn/keys/${id}`, { method: "DELETE", ...jsonBody({ password }) });
}
// Workstation client (hyperlite): settings, and approval of a sign-in code from the web session.
export async function fetchWorkstationConfig() {
  return realFetch("/workstation/config");
}
export async function fetchVmAccess(name) {
  return realFetch(`/vms/${encodeURIComponent(name)}/access`);
}
export async function fetchCliRequest(code) {
  return realFetch(`/auth/cli/requests/${encodeURIComponent(code)}`);
}
export async function decideCliRequest(code, approve) {
  return realFetch(`/auth/cli/requests/${encodeURIComponent(code)}/${approve ? "approve" : "deny"}`, { method: "POST" });
}

// External metric servers the collector pushes to (InfluxDB 2, Graphite); admin only.
export async function fetchMetricServers() {
  return realList("/metric-servers");
}
export async function createMetricServer(payload) {
  return realFetch("/metric-servers", { method: "POST", ...jsonBody(payload) });
}
export async function updateMetricServer(id, payload) {
  return realFetch(`/metric-servers/${id}`, { method: "PUT", ...jsonBody(payload) });
}
export async function deleteMetricServer(id) {
  return realFetch(`/metric-servers/${id}`, { method: "DELETE" });
}
export async function testMetricServer(id) {
  return realFetch(`/metric-servers/${id}/test`, { method: "POST" });
}
// The API documentation inside the dashboard (Administration › API): who may read it, and its schema.
// Shadow mode of hyperlite-cfs (admin): its report, and the copy of SQLite into the daemon.
export async function fetchCfsShadow() {
  return realFetch("/cfs/shadow");
}
export async function seedCfsShadow() {
  return realFetch("/cfs/shadow/seed", { method: "POST" });
}
export async function turnCfsShadowOn() {
  return realFetch("/cfs/shadow/enable", { method: "POST" });
}
export async function turnCfsShadowOff() {
  return realFetch("/cfs/shadow/disable", { method: "POST" });
}
// The cluster (admin, app/core/cluster_setup.py): create one, let a node in, join one, remove a member.
export async function fetchCluster() {
  return realFetch("/cluster");
}
export async function createCluster({ name, address }) {
  return realFetch("/cluster/create", { method: "POST", ...jsonBody({ name: name.trim(), address: address.trim() }) });
}
export async function clusterJoinInformation() {
  return realFetch("/cluster/adhesion", { method: "POST" });
}
export async function joinCluster({ information, address, confirmation }) {
  return realFetch("/cluster/join", { method: "POST", ...jsonBody({ information: information.trim(), address: address.trim(), confirmation: confirmation.trim() }) });
}
// The primary node counts twice in the quorum (null: one vote each).
export async function setClusterPrimary(name) {
  return realFetch("/cluster/principal", { method: "POST", ...jsonBody({ name: name }) });
}
export async function removeClusterMember(name) {
  return realFetch(`/cluster/members/${encodeURIComponent(name)}/remove`, { method: "POST", ...jsonBody({ confirmation: name }) });
}
// Rolling update of the cluster (app/core/rolling_update.py).
export async function fetchClusterUpdatePlan() {
  // Each node reads its repositories: longer than the usual read limit.
  return realFetch("/cluster/update/plan", { signal: AbortSignal.timeout(300000) });
}
export async function startClusterUpdate(stop_blocked = false) {
  return realFetch("/cluster/update", { method: "POST", ...jsonBody({ stop_blocked }) });
}
export async function fetchApiDocsAccess() {
  return realFetch("/api-docs/access");
}
export async function setApiDocsAccess(access) {
  return realFetch("/api-docs/access", { method: "PUT", ...jsonBody({ access }) });
}
// A single-use link to Swagger (/docs), for an account allowed to open it: { url }.
export async function openSwagger() {
  return realFetch("/api-docs/ticket", { method: "POST" });
}
export async function fetchApiTokens() {
  return realList("/auth/tokens");
}
// expiresDays: null for a token that never expires (an explicit choice).
export async function createApiToken(name, expiresDays = 90) {
  return realFetch("/auth/tokens", { method: "POST", ...jsonBody({ name, expires_days: expiresDays }) });
}
export async function deleteApiToken(id) {
  return realFetch(`/auth/tokens/${id}`, { method: "DELETE" });
}

// ---- Compatibility and capabilities ----
export async function fetchNodeCapabilitiesById(nodeId) {
  if (!nodeId || nodeId === "local") return realFetch("/host/capabilities");
  return realFetch(`/nodes/${encodeURIComponent(nodeId)}/capabilities`);
}
export async function fetchHostPreflight() {
  return realFetch("/host/preflight");
}

// This node's database: the full check now, and the online repair of index entries (app/core/db_health.py).
export async function checkHostDatabase() {
  return realFetch("/host/database/check", { method: "POST" });
}
export async function repairHostDatabase() {
  return realFetch("/host/database/repair", { method: "POST" });
}

// ---- Renaming (administrators): storage pools, networks, VM pools, groups, custom roles, jobs, templates, ISOs ----
// everywhere: a shared storage pool is renamed on every node that has it.
export async function renameStoragePool(name, newName, node, everywhere = false) {
  const qs = node && node !== "local" ? `?node=${encodeURIComponent(node)}` : "";
  return realFetch(`/storage/${encodeURIComponent(name)}/rename${qs}`, { method: "POST", ...jsonBody({ new_name: newName, everywhere: everywhere }) });
}
export async function renameNetwork(name, newName) {
  return realFetch(`/networks/${encodeURIComponent(name)}/rename`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
export async function renameVmPool(id, name) {
  return realFetch(`/pools/${id}`, { method: "PATCH", ...jsonBody({ name }) });
}
export async function renameGroup(id, name) {
  return realFetch(`/groups/${id}`, { method: "PATCH", ...jsonBody({ name }) });
}
export async function renameCustomRole(id, name) {
  return realFetch(`/acl/custom-roles/${id}`, { method: "PATCH", ...jsonBody({ name }) });
}
export async function renameJob(id, name) {
  return realFetch(`/jobs/${id}`, { method: "PATCH", ...jsonBody({ name }) });
}
export async function renameApiToken(id, name) {
  return realFetch(`/auth/tokens/${id}`, { method: "PATCH", ...jsonBody({ name }) });
}
export async function renameTemplate(name, newName) {
  return realFetch(`/templates/${encodeURIComponent(name)}/rename`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
export async function renameIso(filename, newName) {
  return realFetch(`/isos/${encodeURIComponent(filename)}/rename`, { method: "POST", ...jsonBody({ new_name: newName }) });
}
