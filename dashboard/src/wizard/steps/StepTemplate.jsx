import { useEffect, useState } from "react";
import { fetchIsoTemplates } from "../../api/client";

// L'image de base (cloud-init Debian 12) est toujours utilisee cote backend reel
// (app/core/vm_builder.py BASE_IMAGE) ; l'ISO ici correspond au champ optionnel
// `iso` de POST /vms (media de demarrage additionnel, deja fonctionnel).
export default function StepTemplate({ form, patch }) {
  const [isos, setIsos] = useState([]);
  useEffect(() => { fetchIsoTemplates().then(setIsos); }, []);

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-200">
        Image de base : <span className="text-anthracite-100 font-medium">Debian 12 (cloud-init)</span>
        <div className="text-xs text-anthracite-400 mt-0.5">Toujours utilisee par Hyperlite pour le disque systeme.</div>
      </div>
      <p className="text-sm text-anthracite-300">ISO a monter au demarrage (optionnel) :</p>
      <label className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${!form.iso ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600"}`}>
        <input type="radio" checked={!form.iso} onChange={() => patch({ iso: "" })} className="accent-accent-blue" />
        <span className="text-sm text-anthracite-100">Aucune</span>
      </label>
      {isos.map((iso) => (
        <label key={iso.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${form.iso === iso.nom ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"}`}>
          <input type="radio" checked={form.iso === iso.nom} onChange={() => patch({ iso: iso.nom })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-anthracite-100">{iso.nom}</div>
            <div className="text-xs text-anthracite-400">{iso.type} -- {iso.taille_mo} Mo</div>
          </div>
        </label>
      ))}
    </div>
  );
}
