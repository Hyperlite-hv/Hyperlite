import { create } from "zustand";
import {
  fetchNodes, fetchVMs, fetchStoragePools, fetchNetworks,
  startVM, stopVM, restartVM, deleteVM, updateVM, fetchVM, makeTaskId,
} from "../api/client";

const TASK_LABELS = {
  start_vm: "Starting",
  stop_vm: "Stopping",
  restart_vm: "Restarting",
  delete_vm: "Deleting",
  create_vm: "VM creation",
  update_vm: "Resource update",
  create_snapshot: "Snapshot creation",
  upload_iso: "ISO upload",
};

let toastCounter = 0;

export const useInfraStore = create((set, get) => ({
  // ---- Data ----
  nodes: [],
  vms: [],
  storagePools: [],
  networks: [],
  loading: true,
  error: null,

  // ---- Selection / navigation ----
  selection: { type: "datacenter", id: null }, // { type: "datacenter" | "node" | "vm", id }
                                               // CentralPanel tab requested from outside the tree (see Header.jsx, the horizontal
                                               // nav bar): CentralPanel reads it when the selection changes, then consumes it
                                               // (clearPendingTab) so it does not force that tab again at every re-render.
  pendingTab: null,
  searchQuery: "",
  treeFilter: "server", // "server" | "pool" | "tag"
                        // The CentralPanel tab actually displayed right now (with the unified sidebar):
                        // purely so that SidebarRail can highlight the active entry without duplicating
                        // the logic already handled by CentralPanel (pendingTab/clearPendingTab,
                        // unchanged). CentralPanel remains the only writer (setActiveTab).
  activeTab: "summary",
  // Mobile sidebar open/collapse state lives in shadcn's <SidebarProvider> (see
  // AppShell.jsx / useSidebar()), not here.

  // ---- Tasks (real actions of this session) & notifications ----
  tasks: [],
  toasts: [],
  taskLogCollapsed: false,

  // ---- Theme ----
  // Light mode is the default of the Hyperlite identity. The real .dark class on
  // <html> is already set before the first render by the inline script of index.html
  // (which avoids the flash), so the state is just aligned here.
  theme: (typeof document !== "undefined" && document.documentElement.classList.contains("dark")) ? "dark" : "light",

  // ---- Initial loading ----
  async loadAll() {
    set({ loading: true, error: null });
    try {
      const [nodes, vms, storagePools, networks] = await Promise.all([
        fetchNodes(), fetchVMs(), fetchStoragePools(), fetchNetworks(),
      ]);
      set({ nodes, vms, storagePools, networks, loading: false });
    } catch (e) {
      set({ error: e.message, loading: false });
    }
  },

  // ---- Silent refresh (background polling) ----
  // The same requests as loadAll, but never touching `loading`/`error`: polling that
  // triggered the big full-screen spinner every 6 s (or wiped the display on a
  // one-off network failure) would be worse than no refresh at all. The goal is to
  // see the changes made by another user (or from another tab) without having to
  // reload the page by hand.
  async refreshAll() {
    try {
      const [nodes, vms, storagePools, networks] = await Promise.all([
        fetchNodes(), fetchVMs(), fetchStoragePools(), fetchNetworks(),
      ]);
      set({ nodes, vms, storagePools, networks });
    } catch {
      // Silent failure: keep the last known state rather than break the display for a
      // network blip; the next tick will retry.
    }
  },

  select(type, id) {
    set({ selection: { type, id } });
  },

  // Select a resource AND request a specific CentralPanel tab in a single call (see
  // Header.jsx): select() alone cannot target a tab, since CentralPanel always falls
  // back to "summary" when the selection changes.
  navigateTo(type, id, tab) {
    set({ selection: { type, id }, pendingTab: tab });
  },

  clearPendingTab() {
    set({ pendingTab: null });
  },

  setActiveTab(tab) {
    set({ activeTab: tab });
  },

  setSearchQuery(q) {
    set({ searchQuery: q });
  },

  setTreeFilter(f) {
    set({ treeFilter: f });
  },

  toggleTheme() {
    const next = get().theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try { localStorage.setItem("hyperlite-theme", next); } catch { /* storage unavailable (private mode): the theme still applies for this session */ }
    set({ theme: next });
  },

  toggleTaskLog() {
    set((s) => ({ taskLogCollapsed: !s.taskLogCollapsed }));
  },

  // ---- Toasts ----
  pushToast(toast) {
    toastCounter += 1;
    const id = `toast-${toastCounter}`;
    set((s) => ({ toasts: [...s.toasts, { id, ...toast }] }));
    // Errors stay displayed until they are closed manually: a technical failure
    // message (e.g. a libvirt error) takes more than 5 s to read, and having it vanish
    // by itself gave the impression that no error had been reported when it had (just
    // too quickly to be seen).
    if (toast.kind !== "error") {
      setTimeout(() => get().dismissToast(id), toast.duration ?? 5000);
    }
    return id;
  },
  dismissToast(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },

  // ---- Tasks: reflections of real actions taken in this session (no persisted
  // history here: see the "Journal" view, which reads the real audit_log on the
  // backend through GET /audit) ----
  addTask(task) {
    const full = {
      id: task.id ?? makeTaskId(),
      statut: "en_cours",
      progres: 0,
      debut: Date.now(),
      fin: null,
      utilisateur: "admin",
      ...task,
    };
    set((s) => ({ tasks: [full, ...s.tasks] }));
    return full.id;
  },
  updateTaskProgress(id, progres) {
    set((s) => ({ tasks: s.tasks.map((t) => (t.id === id ? { ...t, progres } : t)) }));
  },
  completeTask(id, statut = "termine", erreur = null) {
    set((s) => ({
      tasks: s.tasks.map((t) => (t.id === id ? { ...t, statut, progres: 100, fin: Date.now(), erreur } : t)),
    }));
    const task = get().tasks.find((t) => t.id === id);
    if (task) {
      const label = TASK_LABELS[task.type] || task.type;
      get().pushToast({
        kind: statut === "termine" ? "success" : "error",
        title: statut === "termine" ? `${label} finished` : `${label} failed`,
        message: statut === "termine" ? task.cible : (erreur || "An error occurred"),
      });
    }
  },

  addVM(vm) {
    set((s) => ({ vms: [...s.vms, vm] }));
  },

  // ---- VM actions ----
  async runVMAction(vmName, action, { force = false } = {}) {
    const vm = get().vms.find((v) => v.nom === vmName);
    const node = vm?.node;
    const typeMap = { start: "start_vm", stop: "stop_vm", restart: "restart_vm", delete: "delete_vm" };
    const taskType = typeMap[action];
    const taskId = get().addTask({ type: taskType, cible: vmName, node });

    const apiFn = { start: startVM, stop: stopVM, restart: restartVM, delete: deleteVM }[action];
    try {
      // These endpoints answer in a single synchronous HTTP request (no real
      // intermediate percentage on the backend side): the task therefore goes straight
      // from "en_cours" to "termine" once the response is received, rather than
      // simulating a fake progress.
      //
      // "stop" (a clean shutdown/ACPI, domain.shutdown() on the backend) is only a
      // REQUEST sent to the guest: the call succeeds as soon as the request is issued,
      // not when the VM has really shut down (which can take a while, or never happen if
      // the guest does not handle ACPI, e.g. stuck on an installer screen). So we rely
      // on the state actually returned by the API instead of optimistically assuming
      // "arrete": otherwise the interface shows a wrong state, which then makes the
      // following actions fail (e.g. deletion, which rightly refuses a VM still running
      // on the server side) with nothing explaining why to the user.
      //
      // node: it used to be resolved above (const node = vm?.node) but never passed to
      // the API call itself, so a VM displayed as remote ALWAYS acted on the local host
      // by mistake (silently: the VM name could simply not exist locally, giving a 404,
      // or worse, coincide with a homonymous local VM).
      const result = action === "stop"
        ? await apiFn(vmName, force, node)
        : await apiFn(vmName, node);
      set((s) => ({
        vms: s.vms.map((v) => (v.nom === vmName ? { ...v, etat: result.etat, ip: result.ip } : v))
          .filter((v) => !(action === "delete" && v.nom === vmName)),
      }));
      get().completeTask(taskId, "termine");

      // Clean shutdown still in progress (the VM is still running right after the
      // request): a single deferred recheck is enough to refresh the display without
      // reloading the page, for the common case where the guest finishes shutting down
      // within the following seconds.
      if (action === "stop" && result.etat === "actif") {
        setTimeout(async () => {
          try {
            const fresh = await fetchVM(vmName);
            set((s) => ({ vms: s.vms.map((v) => (v.nom === vmName ? { ...v, etat: fresh.etat, ip: fresh.ip } : v)) }));
          } catch { /* the VM may have been deleted in the meantime, no consequence */ }
        }, 4000);
      }

      return taskId;
    } catch (e) {
      get().completeTask(taskId, "echec", e.message);
      throw e;
    }
  },

  async updateVMResources(vmName, payload) {
    const vm = get().vms.find((v) => v.nom === vmName);
    const taskId = get().addTask({ type: "update_vm", cible: vmName, node: vm?.node });
    try {
      const updated = await updateVM(vmName, payload);
      set((s) => ({
        vms: s.vms.map((v) => (v.nom === vmName ? { ...v, vcpu: updated.vcpu, memoire_mo: updated.memoire_mo } : v)),
      }));
      get().completeTask(taskId, "termine");
    } catch (e) {
      get().completeTask(taskId, "echec", e.message);
      throw e;
    }
  },
}));
