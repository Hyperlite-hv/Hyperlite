import { useInfraStore } from "../../store/useInfraStore";

// Matches GET /storage (pools) + GET /storage/{pool}/volumes.
export default function NodeDiskTab({ resource: node }) {
  const storagePools = useInfraStore((s) => s.storagePools);
  if (!node) return null;
  const pools = storagePools.filter((p) => p.node === node.id);

  return (
    <div className="card divide-y divide-anthracite-600">
      <div className="grid grid-cols-4 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
        <span>Pool</span><span>Type</span><span>Capacity</span><span>Available</span>
      </div>
      {pools.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No pools on this node.</div>}
      {pools.map((p) => (
        <div key={p.nom} className="grid grid-cols-4 gap-2 px-4 py-2.5 text-sm">
          <span className="text-anthracite-100">{p.nom}</span>
          <span className="text-anthracite-300">{p.type}</span>
          <span className="text-anthracite-300">{p.capacite_go} GB</span>
          <span className="text-anthracite-300">{p.disponible_go} GB</span>
        </div>
      ))}
    </div>
  );
}
