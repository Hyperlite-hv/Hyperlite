// Miroir cote client de app/core/unattended_install.detect_os_family : sert
// uniquement a adapter le texte du wizard (le backend refait sa propre
// detection, c'est la seule qui compte pour le comportement reel).
const KICKSTART_FAMILIES = ["rhel", "centos", "rocky", "almalinux", "alma-", "fedora"];
const AUTOINSTALL_FAMILIES = ["ubuntu"];

export function detectOsFamily(isoFilename) {
  if (!isoFilename) return null;
  const name = isoFilename.toLowerCase();
  if (KICKSTART_FAMILIES.some((k) => name.includes(k))) return "kickstart";
  if (AUTOINSTALL_FAMILIES.some((k) => name.includes(k))) return "autoinstall";
  return null;
}
