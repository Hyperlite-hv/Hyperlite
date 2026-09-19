// Mise en forme du profil de capacites (GET /host/capabilities,
// GET /nodes/{name}/capabilities) -- mandat portabilite, chantier 4.
// Un champ null/undefined = "non detecte" (jamais suppose : nœud distant
// sans SSH, etc.) et s'affiche "--".

export const NA = "--";

function version(v) {
  if (v == null) return NA;
  return `${Math.floor(v / 1000000)}.${Math.floor(v / 1000) % 1000}.${v % 1000}`;
}

function yesNo(v, yes = "Oui", no = "Non") {
  if (v == null) return NA;
  return v ? yes : no;
}

function go(v) {
  return v == null ? NA : `${v} Go`;
}

function zfsState(s) {
  if (!s || s.zfs_installe == null) return NA;
  if (s.zfs_disponible) return "Disponible";
  if (s.zfs_installe) return "Installé, module noyau non chargé";
  return "Non installé";
}

const SECURE_BOOT = {
  active: "Actif",
  inactive: "Inactif",
  bios_legacy: "BIOS legacy (pas d'UEFI)",
  uefi_inconnu: "UEFI, état inconnu",
};

const BINARIES = ["qemu-img", "virsh", "zfs", "zpool", "git", "gh", "xorriso", "nginx"];

// Lignes plates [{section, key, label, value}] -- `key` stable, sert a
// comparer les nœuds entre eux (tableau comparatif Datacenter).
export function flattenCapabilities(c) {
  if (!c) return [];
  const rows = [];
  const add = (section, key, label, value) => rows.push({ section, key, label, value: value ?? NA });
  const cpu = c.cpu || {};
  add("Matériel", "cpu.arch", "Architecture", cpu.architecture);
  add("Matériel", "cpu.model", "Modèle CPU", cpu.modele);
  add("Matériel", "cpu.cores", "Cœurs logiques", cpu.coeurs_logiques);
  add("Matériel", "cpu.virt", "Virtualisation matérielle (KVM)", yesNo(cpu.virtualisation_materielle));
  add("Matériel", "cpu.numa", "Nœuds NUMA", cpu.numa_noeuds);
  const mem = c.memoire || {};
  add("Matériel", "mem.total", "Mémoire totale", mem.totale_mo != null ? `${mem.totale_mo} Mo` : NA);
  add("Matériel", "mem.avail", "Mémoire disponible", mem.disponible_mo != null ? `${mem.disponible_mo} Mo` : NA);
  const virt = c.virtualisation || {};
  add("Virtualisation", "virt.type", "Hyperviseur", virt.hyperviseur);
  add("Virtualisation", "virt.libvirt", "Version libvirt", version(virt.version_libvirt));
  add("Virtualisation", "virt.qemu", "Version QEMU", version(virt.version_hyperviseur));
  add("Virtualisation", "virt.lxc", "Conteneurs LXC", yesNo(virt.conteneurs_lxc_disponibles, "Disponibles", "Indisponibles"));
  const st = c.stockage || {};
  add("Stockage", "st.zfs", "ZFS", zfsState(st));
  add("Stockage", "st.pool_total", "Pool par défaut : total", go(st.pool_defaut_total_go));
  add("Stockage", "st.pool_free", "Pool par défaut : libre", go(st.pool_defaut_disponible_go));
  const net = c.reseau || {};
  add("Réseau", "net.ifaces", "Interfaces",
    net.interfaces ? net.interfaces.map((i) => `${i.nom}${i.pont ? " (pont)" : ""}${i.vlan ? " (VLAN)" : ""} ${i.etat}`).join(", ") || "aucune" : NA);
  add("Réseau", "net.libvirt", "Réseaux libvirt",
    net.reseaux_libvirt ? net.reseaux_libvirt.map((n) => `${n.nom}${n.actif ? "" : " (inactif)"}`).join(", ") || "aucun" : NA);
  add("Sécurité", "sec.sb", "Secure Boot", c.securite?.secure_boot ? (SECURE_BOOT[c.securite.secure_boot] || c.securite.secure_boot) : NA);
  const sw = c.logiciel || {};
  BINARIES.forEach((b) => add("Logiciel", `sw.${b}`, b, sw[b] == null ? NA : sw[b] ? "Présent" : "Absent"));
  if (sw.dependances_python) {
    Object.entries(sw.dependances_python).forEach(([m, ok]) =>
      add("Dépendances Python", `py.${m}`, m, ok ? "Importable" : "Absent"));
  }
  return rows;
}

// Fonctionnalites deduites du profil : { id, label, etat: "actif"|"limite"|"inconnu", detail }
export function deriveFeatures(c) {
  if (!c) return [];
  const f = [];
  const virt = c.cpu?.virtualisation_materielle;
  f.push({
    id: "kvm", label: "Machines virtuelles KVM",
    etat: virt == null ? "inconnu" : virt ? "actif" : "limite",
    detail: virt === false ? "Virtualisation matérielle absente ou désactivée (BIOS, imbrication) : seuls les conteneurs LXC restent utilisables" : null,
  });
  const lxc = c.virtualisation?.conteneurs_lxc_disponibles;
  f.push({
    id: "lxc", label: "Conteneurs LXC",
    etat: lxc == null ? "inconnu" : lxc ? "actif" : "limite",
    detail: lxc === false ? "Pilote libvirt LXC absent (paquet libvirt-daemon-driver-lxc)" : null,
  });
  const st = c.stockage || {};
  f.push({
    id: "zfs", label: "Stockage ZFS (zvols, snapshots)",
    etat: st.zfs_disponible == null ? "inconnu" : st.zfs_disponible ? "actif" : "limite",
    detail: st.zfs_disponible === false
      ? (st.zfs_installe ? "Module noyau non chargé"
        + (c.securite?.secure_boot === "active" ? " : Secure Boot actif, clé MOK à enrôler" : "")
        : "ZFS non installé") : null,
  });
  const sw = c.logiciel || {};
  if ("bibliotheque_websocket" in sw) {
    f.push({
      id: "ws", label: "Shell hôte, consoles et terminaux (WebSocket)",
      etat: sw.bibliotheque_websocket ? "actif" : "limite",
      detail: sw.bibliotheque_websocket ? null : "Bibliothèque WebSocket absente (uvicorn[standard])",
    });
  }
  const py = sw.dependances_python;
  if (py) {
    const ok = py.pyotp && py.qrcode;
    f.push({ id: "2fa", label: "Authentification 2FA", etat: ok ? "actif" : "limite", detail: ok ? null : "pyotp/qrcode absents" });
  }
  return f;
}

// Lignes du tableau comparatif : pour chaque `key`, valeur par nœud +
// drapeau `differe` (>= 2 valeurs distinctes hors "--"). Les lignes
// "identite" (modele CPU, RAM, disque, ifaces, reseaux) different presque
// toujours entre machines sans que ca bloque quoi que ce soit : marquees
// `informatif` pour ne pas noyer les vraies incompatibilites.
const INFORMATIF = new Set([
  "cpu.model", "cpu.cores", "cpu.numa", "mem.total", "mem.avail", "st.pool_total", "st.pool_free",
  "net.ifaces", "net.libvirt",
  "sw.git", "sw.gh", "sw.nginx", "sw.xorriso", "sw.zfs", "sw.zpool",
]);

export function compareNodes(profiles) {
  const names = Object.keys(profiles);
  const flat = {};
  names.forEach((n) => { flat[n] = flattenCapabilities(profiles[n]); });
  const order = [];
  const meta = {};
  names.forEach((n) => flat[n].forEach((r) => {
    if (!meta[r.key]) { meta[r.key] = { section: r.section, label: r.label }; order.push(r.key); }
  }));
  return order.map((key) => {
    const values = {};
    names.forEach((n) => { values[n] = flat[n].find((r) => r.key === key)?.value ?? NA; });
    const distinct = new Set(Object.values(values).filter((v) => v !== NA));
    return { key, ...meta[key], values, differe: distinct.size > 1, informatif: INFORMATIF.has(key) };
  });
}
