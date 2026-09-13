import { useInfraStore } from "../../store/useInfraStore";

// Correspond a GET /networks, deja fonctionnel cote backend reel (un seul host
// libvirt aujourd'hui, donc la liste est la meme quel que soit le node choisi).
export default function NodeNetworkTab({ resource: node }) {
  const networks = useInfraStore((s) => s.networks);
  if (!node) return null;

  return (
    <div className="card divide-y divide-anthracite-600">
      <div className="grid grid-cols-4 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
        <span>Nom</span><span>Type</span><span>Pont</span><span>Réseau</span>
      </div>
      {networks.map((n) => (
        <div key={n.nom} className="grid grid-cols-4 gap-2 px-4 py-2.5 text-sm">
          <span className="text-anthracite-100">{n.nom}</span>
          <span className="text-anthracite-300 capitalize">{n.type}</span>
          <span className="text-anthracite-300 font-mono">{n.pont}</span>
          <span className="text-anthracite-300 font-mono">{n.reseau ? `${n.reseau.adresse}/${n.reseau.masque}` : "--"}</span>
        </div>
      ))}
    </div>
  );
}
