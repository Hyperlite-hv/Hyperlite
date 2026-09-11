import { useInfraStore } from "../../store/useInfraStore";
import StatusBadge from "../../components/StatusBadge";

// Mock uniquement : Hyperlite pilote un seul host libvirt, pas de cluster/quorum.
// Utile a garder visible pour montrer ou cette notion s'inserait si le projet
// evoluait vers du multi-node reel.
export default function ClusterTab() {
  const nodes = useInfraStore((s) => s.nodes);
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
        Hyperlite ne gere pas de cluster aujourd'hui (un seul host reel : kvm-lab). Vue mock.
      </div>
      <div className="card divide-y divide-anthracite-600">
        {nodes.map((n) => (
          <div key={n.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <StatusBadge etat={n.etat} />
            <span className="text-anthracite-100">{n.nom}</span>
            <span className="ml-auto text-anthracite-400 text-xs">Quorum : OK (simule)</span>
          </div>
        ))}
      </div>
    </div>
  );
}
