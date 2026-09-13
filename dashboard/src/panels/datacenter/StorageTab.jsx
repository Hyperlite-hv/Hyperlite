import { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";
import { fetchIsoTemplates, deleteIso } from "../../api/client";

// Pools : vue agregee de GET /storage sur tous les noeuds -- reel pour kvm-lab.
// Images ISO : vraie liste/upload/suppression via GET/POST/DELETE /isos.
export default function StorageTab() {
  const storagePools = useInfraStore((s) => s.storagePools);
  const pushToast = useInfraStore((s) => s.pushToast);
  const isAdmin = useAuthStore(selectIsAdmin);
  const [isos, setIsos] = useState(null);

  const reloadIsos = useCallback(async () => {
    try { setIsos(await fetchIsoTemplates()); }
    catch (e) { pushToast({ kind: "error", title: "Erreur ISO", message: e.message }); }
  }, [pushToast]);

  useEffect(() => { reloadIsos(); }, [reloadIsos]);

  async function handleDelete(nom) {
    try {
      await deleteIso(nom);
      pushToast({ kind: "success", title: "ISO supprimée", message: nom });
      reloadIsos();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la suppression", message: e.message });
    }
  }

  return (
    <div className="space-y-5">
      <div className="card divide-y divide-anthracite-600">
        <div className="grid grid-cols-5 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
          <span>Pool</span><span>Nœud</span><span>Type</span><span>Capacité</span><span>Disponible</span>
        </div>
        {storagePools.map((p) => (
          <div key={`${p.node}-${p.nom}`} className="grid grid-cols-5 gap-2 px-4 py-2.5 text-sm">
            <span className="text-anthracite-100">{p.nom}</span>
            <span className="text-anthracite-300">{p.node}</span>
            <span className="text-anthracite-300">{p.type}</span>
            <span className="text-anthracite-300">{p.capacite_go} Go</span>
            <span className="text-anthracite-300">{p.disponible_go} Go</span>
          </div>
        ))}
      </div>

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Images ISO</h3>
        {isAdmin && <IsoUploadDropzone onDone={reloadIsos} />}
        <div className="mt-4 divide-y divide-anthracite-600">
          {(isos || []).length === 0 && isos != null && <div className="py-3 text-sm text-anthracite-400">Aucune ISO.</div>}
          {(isos || []).map((iso) => (
            <div key={iso.nom} className="flex items-center gap-3 py-2.5 text-sm">
              <span className="text-anthracite-100 flex-1 truncate">{iso.nom}</span>
              <span className="text-anthracite-400 text-xs">{iso.taille_mo} Mo</span>
              {isAdmin && (
                <button className="btn-danger" onClick={() => handleDelete(iso.nom)}><Trash2 size={13} /></button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
