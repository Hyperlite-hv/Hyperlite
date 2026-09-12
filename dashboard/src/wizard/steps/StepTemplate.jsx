import { useEffect, useState } from "react";
import { fetchIsoTemplates } from "../../api/client";

// Deux modes distincts cote backend reel (app/core/vm_builder.py + POST /vms) :
// - "Aucune" ISO -> disque systeme preinstalle (cloud-init Debian 12), acces
//   direct (utilisateur/mot de passe definis ici, terminal SSH web fonctionnel).
// - Un ISO choisi -> disque systeme VIERGE, la VM demarre sur l'ISO pour une
//   installation manuelle (console VNC) ; le compte utilisateur est cree par
//   l'installeur, pas par Hyperlite (pas de cloud-init dans ce cas).
export default function StepTemplate({ form, patch }) {
  const [isos, setIsos] = useState([]);
  useEffect(() => { fetchIsoTemplates().then(setIsos); }, []);

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-200">
        {form.iso ? (
          <>
            Disque systeme : <span className="text-anthracite-100 font-medium">vierge, a installer</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              La VM demarrera sur l'ISO choisi ci-dessous pour une installation manuelle via la console VNC.
              Le compte utilisateur sera cree pendant l'installation (pas de terminal SSH web automatique pour cette VM tant que vous n'y avez pas configure l'acces vous-meme).
            </div>
          </>
        ) : (
          <>
            Image de base : <span className="text-anthracite-100 font-medium">Debian 12 (cloud-init)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">Preinstallee et prete a l'emploi (utilisateur/mot de passe definis a l'etape suivante).</div>
          </>
        )}
      </div>
      <p className="text-sm text-anthracite-300">ISO d'installation (optionnel) :</p>
      <label className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${!form.iso ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600"}`}>
        <input type="radio" checked={!form.iso} onChange={() => patch({ iso: "" })} className="accent-accent-blue" />
        <span className="text-sm text-anthracite-100">Aucune</span>
      </label>
      {isos.map((iso) => (
        <label key={iso.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${form.iso === iso.nom ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"}`}>
          <input type="radio" checked={form.iso === iso.nom} onChange={() => patch({ iso: iso.nom })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-anthracite-100">{iso.nom}</div>
            <div className="text-xs text-anthracite-400">{iso.taille_mo} Mo</div>
          </div>
        </label>
      ))}
    </div>
  );
}
