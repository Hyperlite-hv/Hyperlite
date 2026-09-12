import { useEffect, useState } from "react";
import { fetchIsoTemplates } from "../../api/client";
import { detectOsFamily } from "../../utils/osFamily";

// Trois cas distincts cote backend reel (app/core/vm_builder.py +
// app/core/unattended_install.py + POST /vms) :
// - "Aucune" ISO -> disque systeme preinstalle (cloud-init Debian 12), acces
//   direct (utilisateur/mot de passe definis ici, terminal SSH web fonctionnel).
// - ISO reconnu (RHEL/CentOS/Rocky/Alma/Fedora -> kickstart, Ubuntu ->
//   autoinstall) -> disque VIERGE mais installation automatisee : le compte
//   utilisateur (defini a l'etape suivante) et la cle SSH d'automatisation
//   sont installes automatiquement, terminal SSH web fonctionnel des la fin
//   de l'installation, comme pour le cloud-init.
// - ISO non reconnu -> disque VIERGE, installation manuelle via la console
//   VNC, le compte est cree par l'installeur (pas de terminal SSH web tant
//   que l'acces n'y est pas configure a la main).
export default function StepTemplate({ form, patch }) {
  const [isos, setIsos] = useState([]);
  useEffect(() => { fetchIsoTemplates().then(setIsos); }, []);
  const osFamily = detectOsFamily(form.iso);

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-200">
        {!form.iso ? (
          <>
            Image de base : <span className="text-anthracite-100 font-medium">Debian 12 (cloud-init)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">Preinstallee et prete a l'emploi (utilisateur/mot de passe definis a l'etape suivante).</div>
          </>
        ) : osFamily === "kickstart" ? (
          <>
            Disque systeme : <span className="text-anthracite-100 font-medium">vierge, installation automatisee (Kickstart)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              Le compte defini a l'etape suivante et la cle SSH Hyperlite sont installes automatiquement -- terminal SSH web fonctionnel une fois l'installation terminee, sans intervention.
            </div>
          </>
        ) : osFamily === "autoinstall" ? (
          <>
            Disque systeme : <span className="text-anthracite-100 font-medium">vierge, installation automatisee (autoinstall)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              Le compte defini a l'etape suivante et la cle SSH Hyperlite sont installes automatiquement. Ubuntu demande une confirmation unique ("Continue with autoinstall?") : appuyez une fois sur Entree dans la console VNC au demarrage, le reste est automatique.
            </div>
          </>
        ) : (
          <>
            Disque systeme : <span className="text-anthracite-100 font-medium">vierge, a installer manuellement</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              ISO non reconnu pour l'installation automatisee : la VM demarrera dessus pour une installation manuelle via la console VNC.
              Le compte utilisateur sera cree pendant l'installation (pas de terminal SSH web automatique pour cette VM tant que vous n'y avez pas configure l'acces vous-meme).
            </div>
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
