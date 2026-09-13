import { useEffect, useState } from "react";
import { HardDrive } from "lucide-react";
import { fetchIsoTemplates, fetchVmDisks } from "../../api/client";
import { detectOsFamily } from "../../utils/osFamily";
import VmDiskUploadDropzone from "../../components/VmDiskUploadDropzone";

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
  const [disks, setDisks] = useState([]);
  useEffect(() => { fetchIsoTemplates().then(setIsos); }, []);
  const reloadDisks = () => fetchVmDisks().then(setDisks);
  useEffect(() => { reloadDisks(); }, []);
  const osFamily = detectOsFamily(form.iso);
  const importMode = form.importDisk != null;
  useEffect(() => {
    if (importMode && !form.importDisk && disks.length > 0) patch({ importDisk: disks[0].nom });
  }, [importMode, form.importDisk, disks]);

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          className={!importMode ? "btn-primary flex-1 !py-1.5 text-xs" : "btn-secondary flex-1 !py-1.5 text-xs"}
          onClick={() => patch({ importDisk: "" })}
        >
          Image de base / ISO
        </button>
        <button
          type="button"
          className={importMode ? "btn-primary flex-1 !py-1.5 text-xs" : "btn-secondary flex-1 !py-1.5 text-xs"}
          onClick={() => patch({ iso: "", importDisk: disks[0]?.nom || "__pending__" })}
        >
          Importer un disque existant
        </button>
      </div>

      {importMode ? (
        <div className="space-y-3">
          <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-200">
            Disque système : <span className="text-anthracite-100 font-medium">importé, tel quel</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              La VM démarre directement sur ce disque (déjà un OS et des comptes dessus) : pas de compte à définir
              ici, pas de terminal SSH web automatique tant que la clé Hyperlite n'y est pas déjà présente.
            </div>
          </div>

          {disks.length > 0 && (
            <div className="space-y-1.5">
              {disks.map((d) => (
                <label key={d.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${form.importDisk === d.nom ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"}`}>
                  <input type="radio" checked={form.importDisk === d.nom} onChange={() => patch({ importDisk: d.nom })} className="accent-accent-blue" />
                  <HardDrive size={14} className="text-anthracite-400 shrink-0" />
                  <div>
                    <div className="text-sm text-anthracite-100">{d.nom}</div>
                    <div className="text-xs text-anthracite-400">{d.taille_mo} Mo</div>
                  </div>
                </label>
              ))}
            </div>
          )}

          <VmDiskUploadDropzone onDone={() => reloadDisks().then(() => {})} />
        </div>
      ) : (
      <>
      <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-200">
        {!form.iso ? (
          <>
            Image de base : <span className="text-anthracite-100 font-medium">Debian 12 (cloud-init)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">Préinstallée et prête à l'emploi (utilisateur/mot de passe définis à l'étape suivante).</div>
          </>
        ) : osFamily === "kickstart" ? (
          <>
            Disque système : <span className="text-anthracite-100 font-medium">vierge, installation automatisée (Kickstart)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              Le compte défini à l'étape suivante et la clé SSH Hyperlite sont installés automatiquement -- terminal SSH web fonctionnel une fois l'installation terminée, sans intervention.
            </div>
          </>
        ) : osFamily === "autoinstall" ? (
          <>
            Disque système : <span className="text-anthracite-100 font-medium">vierge, installation automatisée (autoinstall)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              Le compte défini à l'étape suivante et la clé SSH Hyperlite sont installés automatiquement. Ubuntu demande une confirmation unique ("Continue with autoinstall?") : appuyez une fois sur Entrée dans la console VNC au démarrage, le reste est automatique.
            </div>
          </>
        ) : (
          <>
            Disque système : <span className="text-anthracite-100 font-medium">vierge, à installer manuellement</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              ISO non reconnu pour l'installation automatisée : la VM démarrera dessus pour une installation manuelle via la console VNC.
              Le compte utilisateur sera créé pendant l'installation (pas de terminal SSH web automatique pour cette VM tant que vous n'y avez pas configuré l'accès vous-même).
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
      </>
      )}
    </div>
  );
}
