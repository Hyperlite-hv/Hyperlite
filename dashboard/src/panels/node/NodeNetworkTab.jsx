import { useInfraStore } from "../../store/useInfraStore";
import { Card } from "@/components/ui/card";

// Matches GET /networks, already working on the real backend (a single libvirt
// host today, so the list is the same whichever node is chosen).
export default function NodeNetworkTab({ resource: node }) {
  const networks = useInfraStore((s) => s.networks);
  if (!node) return null;

  return (
    <Card className="p-0 divide-y divide-border">
      <div className="grid grid-cols-4 gap-2 px-4 py-2 text-xs font-medium text-muted-foreground">
        <span>Name</span><span>Type</span><span>Bridge</span><span>Network</span>
      </div>
      {networks.map((n) => (
        <div key={n.nom} className="grid grid-cols-4 gap-2 px-4 py-2.5 text-sm transition-colors duration-150 hover:bg-muted/40">
          <span className="text-foreground">{n.nom}</span>
          <span className="text-foreground/80 capitalize">{n.type}</span>
          <span className="text-foreground/80 font-mono">{n.pont}</span>
          <span className="text-foreground/80 font-mono">{n.reseau ? `${n.reseau.adresse}/${n.reseau.masque}` : "--"}</span>
        </div>
      ))}
    </Card>
  );
}
