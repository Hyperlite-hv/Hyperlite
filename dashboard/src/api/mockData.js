// Donnees mockees. Les formes (noms de champs) sont calquees sur la vraie API
// Hyperlite (app/routers/vms.py, storage.py, network.py, isos.py) partout ou un
// equivalent reel existe, pour que api/client.js puisse basculer vers de vrais
// appels fetch() sans que les composants aient a changer. Les elements marques
// `reel: false` (node "kvm-lab-02", conteneurs LXC, cluster, pare-feu,
// permissions, sauvegardes planifiees) n'ont AUCUN equivalent dans le backend
// Hyperlite actuel : ils illustrent la structure Proxmox demandee mais resteront
// mock tant que ces fonctionnalites n'existent pas cote serveur.

export const nodes = [
  {
    id: "kvm-lab",
    nom: "kvm-lab",
    reel: true,
    etat: "online",
    cpu_coeurs: 8,
    cpu_utilisation: 0.34,
    memoire_totale_mo: 32768,
    memoire_utilisee_mo: 14680,
    stockage_total_go: 460,
    stockage_utilise_go: 214,
    uptime_s: 5_184_000,
    ip: "192.168.3.10",
    version: "Hyperlite 1.0 (QEMU/KVM, libvirt)",
    os: "Debian GNU/Linux 12 (bookworm)",
  },
  {
    id: "kvm-lab-02",
    nom: "kvm-lab-02",
    reel: false,
    etat: "online",
    cpu_coeurs: 16,
    cpu_utilisation: 0.12,
    memoire_totale_mo: 65536,
    memoire_utilisee_mo: 9800,
    stockage_total_go: 900,
    stockage_utilise_go: 180,
    uptime_s: 890_000,
    ip: "192.168.3.11",
    version: "Hyperlite 1.0 (noeud fictif, illustre le multi-node)",
    os: "Debian GNU/Linux 12 (bookworm)",
  },
];

export const vms = [
  {
    nom: "demo-vm", node: "kvm-lab", type: "vm", etat: "actif",
    vcpu: 1, memoire_mo: 768, memoire_utilisee_mo: 410, disque_go: 5, disque_utilise_go: 2.1,
    ip: "192.168.122.114", utilisateur_ssh: null, uuid: "e74a9154-5dd6-4f92-9b64-6b7f0b98d2d2",
    os: "Debian 12", uptime_s: 412_300, tags: ["demo"],
  },
  {
    nom: "web-01", node: "kvm-lab", type: "vm", etat: "actif",
    vcpu: 2, memoire_mo: 2048, memoire_utilisee_mo: 1530, disque_go: 20, disque_utilise_go: 11.4,
    ip: "192.168.122.140", utilisateur_ssh: "anthobo", uuid: "a1b2c3d4-0001-4000-8000-000000000001",
    os: "Debian 12", uptime_s: 998_120, tags: ["prod", "web"],
  },
  {
    nom: "web-02", node: "kvm-lab", type: "vm", etat: "actif",
    vcpu: 2, memoire_mo: 2048, memoire_utilisee_mo: 1720, disque_go: 20, disque_utilise_go: 12.8,
    ip: "192.168.122.141", utilisateur_ssh: "anthobo", uuid: "a1b2c3d4-0002-4000-8000-000000000002",
    os: "Debian 12", uptime_s: 998_050, tags: ["prod", "web"],
  },
  {
    nom: "db-primary", node: "kvm-lab", type: "vm", etat: "actif",
    vcpu: 2, memoire_mo: 4096, memoire_utilisee_mo: 3400, disque_go: 40, disque_utilise_go: 28.9,
    ip: "192.168.122.150", utilisateur_ssh: "anthobo", uuid: "a1b2c3d4-0003-4000-8000-000000000003",
    os: "Debian 12", uptime_s: 1_502_400, tags: ["prod", "db"],
  },
  {
    nom: "staging-app", node: "kvm-lab-02", type: "vm", etat: "arrete",
    vcpu: 1, memoire_mo: 1024, memoire_utilisee_mo: 0, disque_go: 10, disque_utilise_go: 6.2,
    ip: null, utilisateur_ssh: "staging", uuid: "a1b2c3d4-0004-4000-8000-000000000004",
    os: "Ubuntu 22.04", uptime_s: 0, tags: ["staging"],
  },
  {
    nom: "test-sandbox", node: "kvm-lab-02", type: "vm", etat: "arrete",
    vcpu: 1, memoire_mo: 512, memoire_utilisee_mo: 0, disque_go: 5, disque_utilise_go: 1.1,
    ip: null, utilisateur_ssh: "hltester", uuid: "a1b2c3d4-0005-4000-8000-000000000005",
    os: "Debian 12", uptime_s: 0, tags: ["dev"],
  },
  {
    nom: "monitoring", node: "kvm-lab", type: "vm", etat: "avertissement",
    vcpu: 2, memoire_mo: 2048, memoire_utilisee_mo: 1980, disque_go: 15, disque_utilise_go: 14.6,
    ip: "192.168.122.160", utilisateur_ssh: "anthobo", uuid: "a1b2c3d4-0006-4000-8000-000000000006",
    os: "Debian 12", uptime_s: 2_004_100, tags: ["prod", "monitoring"],
    alerte: "Disque a 97% d'utilisation",
  },
  {
    nom: "backup-relay", node: "kvm-lab-02", type: "vm", etat: "erreur",
    vcpu: 1, memoire_mo: 1024, memoire_utilisee_mo: 0, disque_go: 10, disque_utilise_go: 3.4,
    ip: null, utilisateur_ssh: "anthobo", uuid: "a1b2c3d4-0007-4000-8000-000000000007",
    os: "Debian 12", uptime_s: 0, tags: ["backup"],
    alerte: "Echec du dernier demarrage (voir Taches)",
  },
];

// Conteneurs LXC -- PAS DE BACKEND HYPERLITE (QEMU/KVM uniquement aujourd'hui).
// Purement illustratif pour respecter la structure Proxmox demandee.
export const containers = [
  {
    nom: "lxc-proxy", node: "kvm-lab", type: "container", etat: "actif",
    vcpu: 1, memoire_mo: 256, memoire_utilisee_mo: 90, disque_go: 4, disque_utilise_go: 1.2,
    ip: "192.168.122.200", os: "Alpine 3.20", uptime_s: 300_000, tags: ["reseau"],
  },
  {
    nom: "lxc-cache", node: "kvm-lab-02", type: "container", etat: "actif",
    vcpu: 1, memoire_mo: 512, memoire_utilisee_mo: 340, disque_go: 8, disque_utilise_go: 3.7,
    ip: "192.168.122.201", os: "Alpine 3.20", uptime_s: 120_000, tags: ["cache"],
  },
];

export const storagePools = [
  { nom: "default", node: "kvm-lab", type: "dir", etat: "actif", capacite_go: 460, disponible_go: 246 },
  { nom: "backups", node: "kvm-lab-02", type: "dir", etat: "actif", capacite_go: 900, disponible_go: 720 },
];

// Forme calquee sur la vraie reponse de GET /networks (reseau est un objet
// {adresse, masque}, pas une chaine CIDR).
export const networks = [
  { nom: "default", type: "nat", pont: "virbr0", actif: true, reseau: { adresse: "192.168.122.1", masque: "255.255.255.0" } },
  { nom: "hyperlite-isolated", type: "isole", pont: "virbr-hlisol", actif: true, reseau: { adresse: "192.168.100.1", masque: "255.255.255.0" } },
];

export const isoTemplates = [
  { nom: "debian-12-generic-amd64.qcow2", taille_mo: 420, type: "image cloud-init" },
  { nom: "ubuntu-22.04-server.iso", taille_mo: 1450, type: "iso" },
  { nom: "alpine-3.20.iso", taille_mo: 65, type: "iso" },
];

let taskCounter = 0;
export function makeTaskId() {
  taskCounter += 1;
  return `task-${Date.now()}-${taskCounter}`;
}

// Historique de taches initial (le panneau bas + Node > Taches s'appuient dessus,
// et useTaskSimulator.js y ajoute des entrees en direct pour les actions demo).
export const initialTasks = [
  { id: makeTaskId(), type: "start_vm", cible: "web-01", node: "kvm-lab", statut: "termine", progres: 100, debut: Date.now() - 3_600_000, fin: Date.now() - 3_598_000, utilisateur: "admin" },
  { id: makeTaskId(), type: "create_snapshot", cible: "db-primary", node: "kvm-lab", statut: "termine", progres: 100, debut: Date.now() - 1_800_000, fin: Date.now() - 1_799_500, utilisateur: "admin" },
  { id: makeTaskId(), type: "upload_iso", cible: "ubuntu-22.04-server.iso", node: "kvm-lab", statut: "termine", progres: 100, debut: Date.now() - 900_000, fin: Date.now() - 870_000, utilisateur: "admin" },
  { id: makeTaskId(), type: "start_vm", cible: "backup-relay", node: "kvm-lab-02", statut: "echec", progres: 40, debut: Date.now() - 600_000, fin: Date.now() - 598_000, utilisateur: "admin", erreur: "Echec d'allocation memoire sur le noeud" },
];
