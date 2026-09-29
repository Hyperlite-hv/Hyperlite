import { vmActionState } from "./capabilities";

// Bulk actions on the VM list: which of the selected VMs an action applies to, and running it on each of them.

export const BULK_ACTIONS = ["start", "stop", "force-stop", "restart", "migrate", "delete"];

// Whether one VM can take a bulk action, with the same rules as its own menu (capabilities.js). Migration also
// needs a destination other than the VM's own node.
export function bulkEligible(action, vm, caps, targetNode = null) {
  if (action === "migrate") {
    if (!caps.admin || vm.etat !== "actif") return false;
    return targetNode == null || vm.node !== targetNode;
  }
  return vmActionState(action, vm, caps).enabled;
}

// Split a selection into the VMs the action applies to and the ones it skips (already in the state asked, not
// running, lacking the right...), so the confirmation can say both instead of failing half the calls.
export function splitSelection(action, vms, caps, targetNode = null) {
  const apply = [];
  const skip = [];
  for (const vm of vms) (bulkEligible(action, vm, caps, targetNode) ? apply : skip).push(vm);
  return { apply, skip };
}

// Run `fn` on every item, `limit` at a time: a host serves several requests at once, but fifty starts fired
// together would all compete for the same disks. One failure never stops the others; every result is kept.
export async function runBulk(items, fn, limit = 3) {
  const ok = [];
  const failed = [];
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const item = items[next++];
      try {
        await fn(item);
        ok.push(item);
      } catch (error) {
        failed.push({ item, error });
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return { ok, failed };
}

// "a, b, c and 4 more": the names a confirmation shows without growing into a wall of text.
export function nameList(vms, more, max = 8) {
  const names = vms.slice(0, max).map((v) => v.nom);
  return vms.length > max ? `${names.join(", ")} ${more(vms.length - max)}` : names.join(", ");
}
