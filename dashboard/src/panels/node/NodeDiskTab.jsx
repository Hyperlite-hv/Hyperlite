import { useInfraStore } from "../../store/useInfraStore";

// Correspond a GET /storage (pools) + GET /storage/{pool}/volumes, deja
// fonctionnels cote backend reel.
export default function NodeDiskTab({ resource: node }) {
  const storagePools = useInfraStore((s) => s.storagePools);
  if (!node) return null;
  const pools = storagePools.filter((p) => p.node === node.id);

  return (
    <div className="card divide-y divide-anthracite-600">
      <div className="grid grid-cols-4 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
        <span>Pool</span><span>Type</span><span>Capacite</span><span>Disponible</span>
      </div>
      {pools.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucun pool sur ce noeud.</div>}
      {pools.map((p) => (
        <div key={p.nom} className="grid grid-cols-4 gap-2 px-4 py-2.5 text-sm">
          <span className="text-anthracite-100">{p.nom}</span>
          <span className="text-anthracite-300">{p.type}</span>
          <span className="text-anthracite-300">{p.capacite_go} Go</span>
          <span className="text-anthracite-300">{p.disponible_go} Go</span>
        </div>
      ))}
    </div>
  );
}
