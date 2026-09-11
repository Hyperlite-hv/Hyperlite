import { useState } from "react";

// Mock uniquement : Hyperlite n'a pas de reglages "options" persistes par VM
// (demarrage auto, ordre de boot...) aujourd'hui cote backend.
export default function VMOptionsTab({ resource: vm }) {
  const [autostart, setAutostart] = useState(false);
  if (!vm) return null;
  return (
    <div className="card divide-y divide-anthracite-600">
      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">Demarrage automatique</div>
          <div className="text-xs text-anthracite-400">Demarrer cette VM au boot du node</div>
        </div>
        <button
          onClick={() => setAutostart((v) => !v)}
          className={`h-5 w-9 rounded-full transition-colors ${autostart ? "bg-accent-blue" : "bg-anthracite-600"}`}
        >
          <span className={`block h-4 w-4 rounded-full bg-white transition-transform ${autostart ? "translate-x-4" : "translate-x-0.5"}`} />
        </button>
      </div>
      <div className="flex items-center justify-between px-4 py-3">
        <div className="text-sm text-anthracite-100">Ordre de demarrage</div>
        <div className="text-sm text-anthracite-300">Par defaut</div>
      </div>
      <div className="flex items-center justify-between px-4 py-3">
        <div className="text-sm text-anthracite-100">Nom d'hote</div>
        <div className="text-sm text-anthracite-300 font-mono">{vm.nom}</div>
      </div>
    </div>
  );
}
