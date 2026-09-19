// Warning (never blocking) when the entered values exceed the host's PHYSICAL
// resources, only possible with the "overcommit" or "free" allocation policy
// (GET /host/limits: policy + physical).
export default function OverallocationNote({ limits, vcpu, memoryMb, diskGb }) {
  const phys = limits?.physique;
  if (!phys || limits.politique?.actif === "limites") return null;
  const over = [];
  if (phys.vcpu != null && vcpu > phys.vcpu) over.push(`${vcpu} vCPU for ${phys.vcpu} cores`);
  if (phys.memoire_mo != null && memoryMb > phys.memoire_mo) over.push(`${memoryMb} MB for ${phys.memoire_mo} MB of RAM`);
  if (phys.disque_go != null && diskGb > phys.disque_go) over.push(`${diskGb} GB for ${phys.disque_go} GB free (thin disk)`);
  if (over.length === 0) return null;
  return (
    <div className="rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-xs text-status-warning">
      Overcommit ({limits.politique.actif}) : {over.join(", ")}). The VM may fail to start or saturate the host if all the resources are used at the same time.
    </div>
  );
}
