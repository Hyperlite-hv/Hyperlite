import { useInfraStore } from "../../store/useInfraStore";
import { Card } from "@/components/ui/card";

// Matches GET /storage (pools) + GET /storage/{pool}/volumes.
export default function NodeDiskTab({ resource: node }) {
  const storagePools = useInfraStore((s) => s.storagePools);
  if (!node) return null;
  const pools = storagePools.filter((p) => p.node === node.id);

  return (
    <Card className="p-0 divide-y divide-border">
      <div className="grid grid-cols-4 gap-2 px-4 py-2 text-xs font-medium text-muted-foreground">
        <span>Pool</span><span>Type</span><span>Capacity</span><span>Available</span>
      </div>
      {pools.length === 0 && <div className="px-4 py-3 text-sm text-muted-foreground">No pools on this node.</div>}
      {pools.map((p) => (
        <div key={p.nom} className="grid grid-cols-4 gap-2 px-4 py-2.5 text-sm transition-colors duration-150 hover:bg-muted/40">
          <span className="text-foreground">{p.nom}</span>
          <span className="text-foreground/80">{p.type}</span>
          <span className="text-foreground/80">{p.capacite_go} GB</span>
          <span className="text-foreground/80">{p.disponible_go} GB</span>
        </div>
      ))}
    </Card>
  );
}
