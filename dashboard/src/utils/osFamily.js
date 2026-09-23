// Client-side mirror of app/core/unattended_install.detect_os_family: used only to
// adapt the wizard text (the backend does its own detection, which is the only one
// that matters for the real behaviour).
const KICKSTART_FAMILIES = ["rhel", "centos", "rocky", "almalinux", "alma-", "fedora"];
const AUTOINSTALL_FAMILIES = ["ubuntu"];

export function detectWindows(isoFilename) {
  if (!isoFilename) return false;
  const name = isoFilename.toLowerCase();
  return /windows|(?:^|[^a-z0-9])(?:win|server)[-_ ]?(?:10|11|2016|2019|2022|2025)(?:[^0-9]|$)/.test(name);
}

export function isWindowsInstall(form) {
  return guestProfile(form) === "windows";
}

export function guestProfile(form) {
  if (!form.iso || form.importDisk != null) return "linux";
  if (form.guestOs && form.guestOs !== "auto") return form.guestOs;
  if (detectWindows(form.iso)) return "windows";
  return /ubuntu|debian|rhel|centos|rocky|alma|fedora|opensuse|sles|archlinux|alpine/i.test(form.iso) ? "linux" : "other";
}

export function installationFamily(form) {
  return guestProfile(form) === "linux" ? detectOsFamily(form.iso) : null;
}

export function diskController(form) {
  if (form.diskController && form.diskController !== "auto") return form.diskController;
  return guestProfile(form) === "linux" ? "virtio-scsi" : "sata";
}

export function detectOsFamily(isoFilename) {
  if (!isoFilename) return null;
  const name = isoFilename.toLowerCase();
  if (KICKSTART_FAMILIES.some((k) => name.includes(k))) return "kickstart";
  if (AUTOINSTALL_FAMILIES.some((k) => name.includes(k))) return "autoinstall";
  return null;
}
