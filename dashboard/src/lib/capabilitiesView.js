// Formatting of the capability profile (GET /host/capabilities,
// GET /nodes/{name}/capabilities). A null/undefined field means "not detected"
// (never assumed: remote node without SSH, etc.) and is displayed as "--".

export const NA = "--";

function version(v) {
  if (v == null) return NA;
  return `${Math.floor(v / 1000000)}.${Math.floor(v / 1000) % 1000}.${v % 1000}`;
}

function yesNo(v, yes = "Yes", no = "No") {
  if (v == null) return NA;
  return v ? yes : no;
}

function go(v) {
  return v == null ? NA : `${v} GB`;
}

function zfsState(s) {
  if (!s || s.zfs_installe == null) return NA;
  if (s.zfs_disponible) return "Available";
  if (s.zfs_installe) return "Installed, kernel module not loaded";
  return "Not installed";
}

const SECURE_BOOT = {
  active: "Active",
  inactive: "Inactive",
  bios_legacy: "Legacy BIOS (no UEFI)",
  uefi_inconnu: "UEFI, state unknown",
};

const BINARIES = ["qemu-img", "virsh", "zfs", "zpool", "git", "gh", "xorriso", "nginx"];

// Flat rows [{section, key, label, value}]: `key` is stable and is used to compare
// nodes with each other (Datacenter comparison table).
export function flattenCapabilities(c) {
  if (!c) return [];
  const rows = [];
  const add = (section, key, label, value) => rows.push({ section, key, label, value: value ?? NA });
  const cpu = c.cpu || {};
  add("Hardware", "cpu.arch", "Architecture", cpu.architecture);
  add("Hardware", "cpu.model", "CPU model", cpu.modele);
  add("Hardware", "cpu.cores", "Logical cores", cpu.coeurs_logiques);
  add("Hardware", "cpu.virt", "Hardware virtualization (KVM)", yesNo(cpu.virtualisation_materielle));
  add("Hardware", "cpu.numa", "NUMA nodes", cpu.numa_noeuds);
  const mem = c.memoire || {};
  add("Hardware", "mem.total", "Total memory", mem.totale_mo != null ? `${mem.totale_mo} MB` : NA);
  add("Hardware", "mem.avail", "Available memory", mem.disponible_mo != null ? `${mem.disponible_mo} MB` : NA);
  const virt = c.virtualisation || {};
  add("Virtualization", "virt.type", "Hypervisor", virt.hyperviseur);
  add("Virtualisation", "virt.libvirt", "Version libvirt", version(virt.version_libvirt));
  add("Virtualisation", "virt.qemu", "Version QEMU", version(virt.version_hyperviseur));
  add("Virtualization", "virt.lxc", "LXC containers", yesNo(virt.conteneurs_lxc_disponibles, "Available", "Unavailable"));
  const st = c.stockage || {};
  add("Storage", "st.zfs", "ZFS", zfsState(st));
  add("Storage", "st.pool_total", "Default pool: total", go(st.pool_defaut_total_go));
  add("Storage", "st.pool_free", "Default pool: free", go(st.pool_defaut_disponible_go));
  const net = c.reseau || {};
  add("Network", "net.ifaces", "Interfaces",
    net.interfaces ? net.interfaces.map((i) => `${i.nom}${i.pont ? " (bridge)" : ""}${i.vlan ? " (VLAN)" : ""} ${i.etat}`).join(", ") || "none" : NA);
  add("Network", "net.libvirt", "libvirt networks",
    net.reseaux_libvirt ? net.reseaux_libvirt.map((n) => `${n.nom}${n.actif ? "" : " (inactive)"}`).join(", ") || "none" : NA);
  add("Security", "sec.sb", "Secure Boot", c.securite?.secure_boot ? (SECURE_BOOT[c.securite.secure_boot] || c.securite.secure_boot) : NA);
  const sw = c.logiciel || {};
  BINARIES.forEach((b) => add("Software", `sw.${b}`, b, sw[b] == null ? NA : sw[b] ? "Present" : "Absent"));
  if (sw.dependances_python) {
    Object.entries(sw.dependances_python).forEach(([m, ok]) =>
      add("Python dependencies", `py.${m}`, m, ok ? "Importable" : "Absent"));
  }
  return rows;
}

// Features derived from the profile: { id, label, etat: "actif"|"limite"|"inconnu", detail }
export function deriveFeatures(c) {
  if (!c) return [];
  const f = [];
  const virt = c.cpu?.virtualisation_materielle;
  f.push({
    id: "kvm", label: "KVM virtual machines",
    etat: virt == null ? "inconnu" : virt ? "actif" : "limite",
    detail: virt === false ? "Hardware virtualization missing or disabled (BIOS, nesting): only LXC containers remain usable" : null,
  });
  const lxc = c.virtualisation?.conteneurs_lxc_disponibles;
  f.push({
    id: "lxc", label: "LXC containers",
    etat: lxc == null ? "inconnu" : lxc ? "actif" : "limite",
    detail: lxc === false ? "libvirt LXC driver missing (package libvirt-daemon-driver-lxc)" : null,
  });
  const st = c.stockage || {};
  f.push({
    id: "zfs", label: "ZFS storage (zvols, snapshots)",
    etat: st.zfs_disponible == null ? "inconnu" : st.zfs_disponible ? "actif" : "limite",
    detail: st.zfs_disponible === false
      ? (st.zfs_installe ? "Kernel module not loaded"
        + (c.securite?.secure_boot === "active" ? ": Secure Boot active, MOK key must be enrolled" : "")
        : "ZFS not installed") : null,
  });
  const sw = c.logiciel || {};
  if ("bibliotheque_websocket" in sw) {
    f.push({
      id: "ws", label: "Host shell, consoles and terminals (WebSocket)",
      etat: sw.bibliotheque_websocket ? "actif" : "limite",
      detail: sw.bibliotheque_websocket ? null : "WebSocket library missing (uvicorn[standard])",
    });
  }
  const py = sw.dependances_python;
  if (py) {
    const ok = py.pyotp && py.qrcode;
    f.push({ id: "2fa", label: "2FA authentication", etat: ok ? "actif" : "limite", detail: ok ? null : "pyotp/qrcode missing" });
  }
  return f;
}

// Rows of the comparison table: for each `key`, a value per node plus a `differe`
// flag (>= 2 distinct values, excluding "--"). The "identity" rows (CPU model,
// RAM, disk, interfaces, networks) almost always differ between machines without
// blocking anything: they are marked `informatif` so they do not drown out the real
// incompatibilities.
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
