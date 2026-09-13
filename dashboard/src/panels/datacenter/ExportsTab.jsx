import { useCallback, useEffect, useState } from "react";
import { PackageOpen, Download, Trash2 } from "lucide-react";
import { fetchVmExports, downloadVmExport, deleteVmExport } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import ConfirmDialog from "../../components/ConfirmDialog";

function formatSize(bytes) {
  if (!bytes) return "--";
  const go = bytes / (1024 ** 3);
  return go >= 1 ? `${go.toFixed(2)} Go` : `${(bytes / (1024 ** 2)).toFixed(0)} Mo`;
}

// Reel : GET /vm-exports (chantier 23) -- fichiers qcow2 produits par
// "Exporter le disque" sur une VM (voir VMActionMenu.jsx), prets a
// telecharger. Meme mecanisme de rafraichissement periodique que
// BackupsTab/ContainersTab tant qu'un export peut etre en cours (pas de
// suivi de progression en direct ici, voir la note dans app/routers/
// vm_export.py -- meme limite deja acceptee pour les sauvegardes).
export default function ExportsTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [toDelete, setToDelete] = useState(null);

  const reload = useCallback(() => {
    fetchVmExports().then(setRows).catch((e) => pushToast({ kind: "error", title: "Erreur exports", message: e.message }));
  }, [pushToast]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    const id = setInterval(reload, 8000);
    return () => clearInterval(id);
  }, [reload]);

  async function handleDownload(nom) {
    try {
      await downloadVmExport(nom);
    } catch (e) {
      pushToast({ kind: "error", title: "Échec du téléchargement", message: e.message });
    }
  }

  async function handleDelete() {
    if (!toDelete) return;
    try {
      await deleteVmExport(toDelete.nom);
      pushToast({ kind: "success", title: "Export supprimé", message: toDelete.nom });
      setToDelete(null);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    }
  }

  if (rows == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  if (rows.length === 0) {
    return (
      <div className="card flex flex-col items-center gap-2 p-8 text-center">
        <PackageOpen size={26} className="text-anthracite-400" />
        <p className="text-sm text-anthracite-300">Aucun export pour le moment.</p>
        <p className="text-xs text-anthracite-500 max-w-sm">
          Depuis une VM, ouvrez son menu d'actions et choisissez "Exporter le disque" pour produire un fichier
          téléchargeable ici (disque système uniquement).
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="card divide-y divide-anthracite-600">
        <div className="grid grid-cols-[1fr_110px_140px_90px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
          <span>Fichier</span><span>Taille</span><span>Créé le</span><span></span>
        </div>
        {rows.map((r) => (
          <div key={r.nom} className="grid grid-cols-[1fr_110px_140px_90px] gap-2 px-4 py-2 text-sm items-center">
            <span className="text-anthracite-100 truncate font-mono text-xs" title={r.nom}>{r.nom}</span>
            <span className="text-xs text-anthracite-400">{formatSize(r.taille_octets)}</span>
            <span className="text-anthracite-400 text-xs font-mono">{new Date(r.modifie_le).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
            <div className="flex justify-end gap-1.5">
              <button className="btn-secondary" title="Télécharger" onClick={() => handleDownload(r.nom)}><Download size={13} /></button>
              <button className="btn-danger" title="Supprimer" onClick={() => setToDelete(r)}><Trash2 size={13} /></button>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={!!toDelete}
        title="Supprimer l'export"
        message={`Supprimer définitivement le fichier "${toDelete?.nom}" ?`}
        confirmLabel="Supprimer"
        onConfirm={handleDelete}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
