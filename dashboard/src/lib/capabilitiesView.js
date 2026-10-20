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
  if (!s || s.zfs_installed == null) return NA;
  if (s.zfs_available) return "Available";
  if (s.zfs_installed) return "Installed, kernel module not loaded";
  return "Not installed";
}

const SECURE_BOOT = {
  active: "Active",
  inactive: "Inactive",
  bios_legacy: "Legacy BIOS (no UEFI)",
  uefi_unknown: "UEFI, state unknown",
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
  add("Hardware", "cpu.model", "CPU model", cpu.model);
  add("Hardware", "cpu.cores", "Logical cores", cpu.logical_cores);
  add("Hardware", "cpu.virt", "Hardware virtualization (KVM)", yesNo(cpu.hardware_virtualization));
  add("Hardware", "cpu.numa", "NUMA nodes", cpu.numa_nodes);
  const mem = c.memory || {};
  add("Hardware", "mem.total", "Total memory", mem.total_mb != null ? `${mem.total_mb} MB` : NA);
  add("Hardware", "mem.avail", "Available memory", mem.available_mb != null ? `${mem.available_mb} MB` : NA);
  const virt = c.virtualization || {};
  add("Virtualization", "virt.type", "Hypervisor", virt.hypervisor);
  add("Virtualisation", "virt.libvirt", "Version libvirt", version(virt.libvirt_version));
  add("Virtualisation", "virt.qemu", "Version QEMU", version(virt.hypervisor_version));
  add("Virtualization", "virt.lxc", "LXC containers", yesNo(virt.lxc_containers_available, "Available", "Unavailable"));
  const st = c.storage || {};
  add("Storage", "st.zfs", "ZFS", zfsState(st));
  add("Storage", "st.pool_total", "Default pool: total", go(st.default_pool_total_gb));
  add("Storage", "st.pool_free", "Default pool: free", go(st.default_pool_available_gb));
  const net = c.network || {};
  add("Network", "net.ifaces", "Interfaces",
    net.interfaces ? net.interfaces.map((i) => `${i.name}${i.bridge ? " (bridge)" : ""}${i.vlan ? " (VLAN)" : ""} ${i.state}`).join(", ") || "none" : NA);
  add("Network", "net.libvirt", "libvirt networks",
    net.libvirt_networks ? net.libvirt_networks.map((n) => `${n.name}${n.active ? "" : " (inactive)"}`).join(", ") || "none" : NA);
  add("Security", "sec.sb", "Secure Boot", c.security?.secure_boot ? (SECURE_BOOT[c.security.secure_boot] || c.security.secure_boot) : NA);
  const sw = c.software || {};
  BINARIES.forEach((b) => add("Software", `sw.${b}`, b, sw[b] == null ? NA : sw[b] ? "Present" : "Absent"));
  if (sw.python_dependencies) {
    Object.entries(sw.python_dependencies).forEach(([m, ok]) =>
      add("Python dependencies", `py.${m}`, m, ok ? "Importable" : "Absent"));
  }
  return rows;
}

// Features derived from the profile: { id, label, etat: "actif"|"limite"|"unknown", detail }
export function deriveFeatures(c) {
  if (!c) return [];
  const f = [];
  const virt = c.cpu?.hardware_virtualization;
  f.push({
    id: "kvm", label: "KVM virtual machines",
    state: virt == null ? "unknown" : virt ? "active" : "limite",
    detail: virt === false ? "Hardware virtualization missing or disabled (BIOS, nesting): only LXC containers remain usable" : null,
  });
  const lxc = c.virtualization?.lxc_containers_available;
  f.push({
    id: "lxc", label: "LXC containers",
    state: lxc == null ? "unknown" : lxc ? "active" : "limite",
    detail: lxc === false ? "libvirt LXC driver missing (package libvirt-daemon-driver-lxc)" : null,
  });
  const st = c.storage || {};
  f.push({
    id: "zfs", label: "ZFS storage (zvols, snapshots)",
    state: st.zfs_available == null ? "unknown" : st.zfs_available ? "active" : "limite",
    detail: st.zfs_available === false
      ? (st.zfs_installed ? "Kernel module not loaded"
        + (c.security?.secure_boot === "active" ? ": Secure Boot active, MOK key must be enrolled" : "")
        : "ZFS not installed") : null,
  });
  const sw = c.software || {};
  if ("websocket_library" in sw) {
    f.push({
      id: "ws", label: "Host shell, consoles and terminals (WebSocket)",
      state: sw.websocket_library ? "active" : "limite",
      detail: sw.websocket_library ? null : "WebSocket library missing (uvicorn[standard])",
    });
  }
  const py = sw.python_dependencies;
  if (py) {
    const ok = py.pyotp && py.qrcode;
    f.push({ id: "2fa", label: "2FA authentication", state: ok ? "active" : "limite", detail: ok ? null : "pyotp/qrcode missing" });
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
