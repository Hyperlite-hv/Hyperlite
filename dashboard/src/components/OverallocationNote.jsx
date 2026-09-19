// Avertissement (jamais bloquant) quand les valeurs saisies depassent le
// PHYSIQUE de l'hote, possible seulement avec la politique d'allocation
// "surallocation" ou "libre" (GET /host/limits : politique + physique).
export default function OverallocationNote({ limits, vcpu, memoryMb, diskGb }) {
  const phys = limits?.physique;
  if (!phys || limits.politique?.actif === "limites") return null;
  const over = [];
  if (phys.vcpu != null && vcpu > phys.vcpu) over.push(`${vcpu} vCPU pour ${phys.vcpu} cœurs`);
  if (phys.memoire_mo != null && memoryMb > phys.memoire_mo) over.push(`${memoryMb} Mo pour ${phys.memoire_mo} Mo de RAM`);
  if (phys.disque_go != null && diskGb > phys.disque_go) over.push(`${diskGb} Go pour ${phys.disque_go} Go libres (disque fin)`);
  if (over.length === 0) return null;
  return (
    <div className="rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-xs text-status-warning">
      Surallocation ({limits.politique.actif}) : {over.join(", ")}. La VM peut ne pas démarrer ou saturer l'hôte si toutes les ressources sont utilisées en même temps.
    </div>
  );
}
