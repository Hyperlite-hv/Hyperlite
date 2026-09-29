// A VM is identified by its node AND its name: two nodes may each run a VM called "test". Selecting,
// linking and acting by name alone opened (and started, stopped or deleted) the local one when the
// remote one was meant, since the local VMs come first in the inventory.
//
// The key is the bare name for a VM of the local host (so every existing link, bookmark and name-only
// reference, like a backup's or an HA entry's, still points to the same VM) and "name@node" for a VM
// of a remote node. "@" cannot appear in a VM or node name, so the key is never ambiguous.
export const LOCAL = "local";

export function vmKey(vm) {
  if (!vm) return null;
  return !vm.node || vm.node === LOCAL ? vm.nom : `${vm.nom}@${vm.node}`;
}

export function parseVmKey(key) {
  const s = String(key ?? "");
  const at = s.lastIndexOf("@");
  return at > 0 ? { nom: s.slice(0, at), node: s.slice(at + 1) } : { nom: s, node: LOCAL };
}

// The VM a key designates. A bare name means the local host's VM, else the only VM with that name.
export function findVm(vms, key) {
  if (key == null) return undefined;
  const { nom, node } = parseVmKey(key);
  const list = vms || [];
  const exact = list.find((v) => v.nom === nom && (v.node || LOCAL) === node);
  if (exact || String(key).includes("@")) return exact;
  const same = list.filter((v) => v.nom === nom);
  return same.length === 1 ? same[0] : undefined;
}

export const sameVm = (a, b) => Boolean(a && b) && vmKey(a) === vmKey(b);
export const isRemoteVm = (vm) => Boolean(vm?.node) && vm.node !== LOCAL;
// The `node` query parameter the API expects: none for the local host.
export const apiNode = (node) => (node && node !== LOCAL ? node : null);
