import { ShieldOff } from "lucide-react";

// Mock uniquement : Hyperlite n'a pas de pare-feu applicatif aujourd'hui
// (la securite reseau repose sur les reseaux libvirt NAT/isole existants).
export default function NodeFirewallTab() {
  return (
    <div className="card flex flex-col items-center gap-2 p-8 text-center">
      <ShieldOff size={26} className="text-anthracite-400" />
      <p className="text-sm text-anthracite-300">Pare-feu non implemente cote backend Hyperlite.</p>
      <p className="text-xs text-anthracite-500 max-w-sm">
        La segmentation reseau actuelle passe par les reseaux libvirt NAT/isole (voir l'onglet Reseau).
      </p>
    </div>
  );
}
