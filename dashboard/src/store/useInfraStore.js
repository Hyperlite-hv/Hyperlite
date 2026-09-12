import { create } from "zustand";
import {
  fetchNodes, fetchVMs, fetchStoragePools, fetchNetworks,
  startVM, stopVM, restartVM, deleteVM, updateVM, fetchVM, makeTaskId,
} from "../api/client";

const TASK_LABELS = {
  start_vm: "Demarrage",
  stop_vm: "Arret",
  restart_vm: "Redemarrage",
  delete_vm: "Suppression",
  create_vm: "Creation VM",
  update_vm: "Modification des ressources",
  create_snapshot: "Creation snapshot",
  upload_iso: "Televersement ISO",
};

let toastCounter = 0;

export const useInfraStore = create((set, get) => ({
  // ---- Donnees ----
  nodes: [],
  vms: [],
  storagePools: [],
  networks: [],
  loading: true,
  error: null,

  // ---- Selection / navigation ----
  selection: { type: "datacenter", id: null }, // { type: "datacenter" | "node" | "vm", id }
  searchQuery: "",
  treeFilter: "server", // "server" | "pool" | "tag"

  // ---- Taches (actions reelles de cette session) & notifications ----
  tasks: [],
  toasts: [],
  taskLogCollapsed: false,

  // ---- Theme ----
  // Le mode clair est le defaut de l'identite Hyperlite ; la classe .dark
  // reelle sur <html> est deja posee avant le premier rendu par le script
  // inline d'index.html (evite le flash), on aligne juste le state ici.
  theme: (typeof document !== "undefined" && document.documentElement.classList.contains("dark")) ? "dark" : "light",

  // ---- Chargement initial ----
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
    try { localStorage.setItem("hyperlite-theme", next); } catch (e) {}
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
    // Les erreurs restent affichees jusqu'a fermeture manuelle : un message
    // d'echec technique (ex. erreur libvirt) prend plus de 5s a lire, et le
    // disparaitre tout seul donnait l'impression qu'aucune erreur n'etait
    // remontee alors qu'elle l'etait (juste trop vite pour etre vue).
    if (toast.kind !== "error") {
      setTimeout(() => get().dismissToast(id), toast.duration ?? 5000);
    }
    return id;
  },
  dismissToast(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },

  // ---- Taches : reflets d'actions reelles prises dans cette session (pas
  // d'historique persiste ici -- voir la vue "Journal", qui lit le vrai
  // audit_log cote backend via GET /audit) ----
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
  async runVMAction(vmName, action, { force = false } = {}) {
    const vm = get().vms.find((v) => v.nom === vmName);
    const node = vm?.node;
    const typeMap = { start: "start_vm", stop: "stop_vm", restart: "restart_vm", delete: "delete_vm" };
    const taskType = typeMap[action];
    const taskId = get().addTask({ type: taskType, cible: vmName, node });

    const apiFn = { start: startVM, stop: stopVM, restart: restartVM, delete: deleteVM }[action];
    try {
      // Ces endpoints repondent en une seule requete HTTP synchrone (pas de
      // pourcentage intermediaire reel cote backend) : la tache passe donc
      // directement de "en_cours" a "termine" une fois la reponse recue,
      // plutot que de simuler une fausse progression.
      //
      // "stop" (arret propre/ACPI, cote backend domain.shutdown()) est une
      // simple DEMANDE envoyee a l'invite : l'appel reussit des que la
      // demande est emise, pas quand la VM s'est reellement eteinte (qui
      // peut prendre du temps, voire ne jamais arriver si l'invite ne gere
      // pas l'ACPI -- ex. bloque sur un ecran d'installeur). On se fie donc
      // a l'etat reellement renvoye par l'API plutot que de supposer
      // "arrete" par optimisme : sinon l'interface affiche un etat faux,
      // qui fait ensuite echouer les actions suivantes (ex. suppression,
      // qui refuse a juste titre une VM encore active cote serveur) sans
      // que rien n'explique pourquoi a l'utilisateur.
      const result = await apiFn(vmName, ...(action === "stop" ? [force] : []));
      set((s) => ({
        vms: s.vms.map((v) => (v.nom === vmName ? { ...v, etat: result.etat, ip: result.ip } : v))
          .filter((v) => !(action === "delete" && v.nom === vmName)),
      }));
      get().completeTask(taskId, "termine");

      // Arret propre encore en cours (VM toujours active juste apres la
      // demande) : une seule revenification differee suffit a rafraichir
      // l'affichage sans avoir a recharger la page, pour le cas courant ou
      // l'invite finit par s'eteindre dans les secondes qui suivent.
      if (action === "stop" && result.etat === "actif") {
        setTimeout(async () => {
          try {
            const fresh = await fetchVM(vmName);
            set((s) => ({ vms: s.vms.map((v) => (v.nom === vmName ? { ...v, etat: fresh.etat, ip: fresh.ip } : v)) }));
          } catch (e) { /* VM peut-etre supprimee entre-temps, sans consequence */ }
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
