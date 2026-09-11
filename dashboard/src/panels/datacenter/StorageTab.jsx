import { useInfraStore } from "../../store/useInfraStore";

// Vue agregee de GET /storage sur tous les noeuds -- reel pour kvm-lab.
export default function StorageTab() {
  const storagePools = useInfraStore((s) => s.storagePools);
  return (
    <div className="card divide-y divide-anthracite-600">
      <div className="grid grid-cols-5 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
        <span>Pool</span><span>Noeud</span><span>Type</span><span>Capacite</span><span>Disponible</span>
      </div>
      {storagePools.map((p) => (
        <div key={`${p.node}-${p.nom}`} className="grid grid-cols-5 gap-2 px-4 py-2.5 text-sm">
          <span className="text-anthracite-100">{p.nom}</span>
          <span className="text-anthracite-300">{p.node}</span>
          <span className="text-anthracite-300">{p.type}</span>
          <span className="text-anthracite-300">{p.capacite_go} Go</span>
          <span className="text-anthracite-300">{p.disponible_go} Go</span>
        </div>
      ))}
    </div>
  );
}
