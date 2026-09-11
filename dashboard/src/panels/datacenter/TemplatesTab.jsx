import { useEffect, useState } from "react";
import { Layers, Rocket, Trash2 } from "lucide-react";
import { fetchTemplates, deployTemplate, deleteTemplate } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import ConfirmDialog from "../../components/ConfirmDialog";

// Reel : GET/POST/DELETE /templates, deja fonctionnels cote backend (la
// conversion VM -> template se fait depuis l'onglet Resume d'une VM arretee).
export default function TemplatesTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const loadAll = useInfraStore((s) => s.loadAll);
  const [templates, setTemplates] = useState(null);
  const [deployTarget, setDeployTarget] = useState(null);
  const [newName, setNewName] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [busy, setBusy] = useState(false);

  const reload = () => fetchTemplates().then(setTemplates).catch((e) => pushToast({ kind: "error", title: "Erreur templates", message: e.message }));
  useEffect(() => { reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (templates == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  async function handleDeploy() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await deployTemplate(deployTarget.nom, newName.trim());
      pushToast({ kind: "success", title: "VM deployee", message: newName.trim() });
      setDeployTarget(null);
      setNewName("");
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Echec du deploiement", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete() {
    setBusy(true);
    try {
      await deleteTemplate(pendingDelete.nom);
      pushToast({ kind: "success", title: "Template supprime", message: pendingDelete.nom });
      setPendingDelete(null);
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Echec de la suppression", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-anthracite-400">Convertir une VM (arretee) en template se fait depuis son onglet Resume.</p>
      <div className="card divide-y divide-anthracite-600">
        {templates.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucun template.</div>}
        {templates.map((t) => (
          <div key={t.nom} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <Layers size={14} className="text-anthracite-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-anthracite-100">{t.nom}</div>
              <div className="text-xs text-anthracite-400 truncate">
                depuis {t.vm_source} -- {t.vcpu} vCPU / {t.memoire_mo} Mo -- cree par {t.cree_par} le {t.cree_le}
              </div>
            </div>
            {isAdmin && (
              <>
                <button className="btn-secondary" disabled={busy} onClick={() => { setDeployTarget(t); setNewName(`${t.nom}-01`); }}>
                  <Rocket size={13} /> Deployer
                </button>
                <button className="btn-danger" disabled={busy} onClick={() => setPendingDelete(t)}><Trash2 size={13} /></button>
              </>
            )}
          </div>
        ))}
      </div>

      {deployTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setDeployTarget(null)}>
          <div className="card w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-anthracite-100 mb-3">Deployer "{deployTarget.nom}"</h3>
            <label className="text-xs font-medium text-anthracite-300">Nom de la nouvelle VM</label>
            <input className="input mt-1" value={newName} onChange={(e) => setNewName(e.target.value)} />
            <div className="mt-4 flex justify-end gap-2">
              <button className="btn-secondary" onClick={() => setDeployTarget(null)}>Annuler</button>
              <button className="btn-primary" disabled={busy || !newName.trim()} onClick={handleDeploy}>Deployer</button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title={`Supprimer le template '${pendingDelete?.nom}' ?`}
        message="Le disque du template sera definitivement supprime."
        confirmLabel="Supprimer"
        onCancel={() => setPendingDelete(null)}
        onConfirm={handleDelete}
      />
    </div>
  );
}
