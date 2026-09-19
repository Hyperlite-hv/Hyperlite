// Client-side mirror of app/core/unattended_install.detect_os_family: used only to
// adapt the wizard text (the backend does its own detection, which is the only one
// that matters for the real behaviour).
const KICKSTART_FAMILIES = ["rhel", "centos", "rocky", "almalinux", "alma-", "fedora"];
const AUTOINSTALL_FAMILIES = ["ubuntu"];

export function detectOsFamily(isoFilename) {
  if (!isoFilename) return null;
  const name = isoFilename.toLowerCase();
  if (KICKSTART_FAMILIES.some((k) => name.includes(k))) return "kickstart";
  if (AUTOINSTALL_FAMILIES.some((k) => name.includes(k))) return "autoinstall";
  return null;
}
