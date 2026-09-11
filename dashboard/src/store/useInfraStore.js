import { create } from "zustand";
import {
  fetchNodes, fetchVMs, fetchContainers, fetchStoragePools, fetchNetworks, fetchTasks,
  startVM, stopVM, restartVM, deleteVM,
} from "../api/client";
import { makeTaskId } from "../api/mockData";

const TASK_LABELS = {
  start_vm: "Demarrage",
  stop_vm: "Arret",
  restart_vm: "Redemarrage",
  delete_vm: "Suppression",
  create_vm: "Creation VM",
  create_snapshot: "Creation snapshot",
  upload_iso: "Televersement ISO",
};

let toastCounter = 0;

export const useInfraStore = create((set, get) => ({
  // ---- Donnees ----
  nodes: [],
  vms: [],
  containers: [],
  storagePools: [],
  networks: [],
  loading: true,
  error: null,

  // ---- Selection / navigation ----
  selection: { type: "datacenter", id: null }, // { type: "datacenter" | "node" | "vm" | "container", id }
  searchQuery: "",
  treeFilter: "server", // "server" | "pool" | "tag"

  // ---- Taches & notifications ----
  tasks: [],
  toasts: [],
  taskLogCollapsed: false,

  // ---- Theme ----
  theme: "dark",

  // ---- Chargement initial ----
  async loadAll() {
    set({ loading: true, error: null });
    try {
      const [nodes, vms, containers, storagePools, networks, tasks] = await Promise.all([
        fetchNodes(), fetchVMs(), fetchContainers(), fetchStoragePools(), fetchNetworks(), fetchTasks(),
      ]);
      set({ nodes, vms, containers, storagePools, networks, tasks, loading: false });
    } catch (e) {
      set({ error: e.message, loading: false });
    }
  },

  select(type, id) {
    set({ selection: { type, id } });
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
    setTimeout(() => get().dismissToast(id), toast.duration ?? 5000);
    return id;
  },
  dismissToast(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },

  // ---- Taches (avec simulation de progression pour l'UX, voir hooks/useTaskSimulator.js) ----
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
        title: statut === "termine" ? `${label} terminee` : `${label} en echec`,
        message: statut === "termine" ? task.cible : (erreur || "Une erreur est survenue"),
      });
    }
  },

  addVM(vm) {
    set((s) => ({ vms: [...s.vms, vm] }));
  },

  // ---- Actions VM ----
  async runVMAction(vmName, action) {
    const vm = get().vms.find((v) => v.nom === vmName);
    const node = vm?.node;
    const typeMap = { start: "start_vm", stop: "stop_vm", restart: "restart_vm", delete: "delete_vm" };
    const taskType = typeMap[action];
    const taskId = get().addTask({ type: taskType, cible: vmName, node });

    const apiFn = { start: startVM, stop: stopVM, restart: restartVM, delete: deleteVM }[action];
    try {
      await apiFn(vmName);
      // La progression "reelle" est simulee par useTaskSimulator (voir ce hook) ;
      // ici on se contente de refleter l'etat final optimiste dans la liste des VMs.
      set((s) => ({
        vms: s.vms.map((v) => {
          if (v.nom !== vmName) return v;
          if (action === "start") return { ...v, etat: "actif" };
          if (action === "stop") return { ...v, etat: "arrete", ip: null };
          if (action === "restart") return { ...v, etat: "actif" };
          return v;
        }).filter((v) => !(action === "delete" && v.nom === vmName)),
      }));
      return taskId;
    } catch (e) {
      get().completeTask(taskId, "echec", e.message);
      throw e;
    }
  },
}));
