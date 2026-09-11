import { ShieldOff } from "lucide-react";

export default function FirewallTab() {
  return (
    <div className="card flex flex-col items-center gap-2 p-8 text-center">
      <ShieldOff size={26} className="text-anthracite-400" />
      <p className="text-sm text-anthracite-300">Pare-feu non implemente cote backend Hyperlite.</p>
    </div>
  );
}
