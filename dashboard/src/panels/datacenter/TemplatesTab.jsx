import { useEffect, useState } from "react";
import { Layers, Rocket, Trash2 } from "lucide-react";
import { fetchTemplates, deployTemplate, deleteTemplate } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import ConfirmDialog from "../../components/ConfirmDialog";

// Real: GET/POST/DELETE /templates, already working on the backend (converting a
// VM to a template is done from the Summary tab of a stopped VM).
export default function TemplatesTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const loadAll = useInfraStore((s) => s.loadAll);
  const [templates, setTemplates] = useState(null);
  const [deployTarget, setDeployTarget] = useState(null);
  const [newName, setNewName] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [busy, setBusy] = useState(false);

  const reload = () => fetchTemplates().then(setTemplates).catch((e) => pushToast({ kind: "error", title: "Templates error", message: e.message }));
  useEffect(() => { reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (templates == null) return <div className="card p-4 text-sm text-anthracite-400">Loading...</div>;

  async function handleDeploy() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await deployTemplate(deployTarget.nom, newName.trim());
      pushToast({ kind: "success", title: "VM deployed", message: newName.trim() });
      setDeployTarget(null);
      setNewName("");
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Deployment failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete() {
    setBusy(true);
    try {
      await deleteTemplate(pendingDelete.nom);
      pushToast({ kind: "success", title: "Template deleted", message: pendingDelete.nom });
      setPendingDelete(null);
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Deletion failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-anthracite-400">Converting a (stopped) VM to a template is done from its Summary tab.</p>
      <div className="card divide-y divide-anthracite-600">
        {templates.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No templates.</div>}
        {templates.map((t) => (
          <div key={t.nom} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <Layers size={14} className="text-anthracite-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-anthracite-100">{t.nom}</div>
              <div className="text-xs text-anthracite-400 truncate">
                from {t.vm_source} -- {t.vcpu} vCPU / {t.memoire_mo} MB, created by {t.cree_par} on {t.cree_le}
              </div>
            </div>
            {isAdmin && (
              <>
                <button className="btn-secondary" disabled={busy} onClick={() => { setDeployTarget(t); setNewName(`${t.nom}-01`); }}>
                  <Rocket size={13} /> Deploy
                </button>
                <button aria-label={`Delete template ${t.nom}`} className="btn-danger" disabled={busy} onClick={() => setPendingDelete(t)}><Trash2 size={13} /></button>
              </>
            )}
          </div>
        ))}
      </div>

      {deployTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setDeployTarget(null)}>
          <div className="card w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-anthracite-100 mb-3">Deploy "{deployTarget.nom}"</h3>
            <label className="text-xs font-medium text-anthracite-300">Name of the new VM</label>
            <input aria-label="Name of the new VM" className="input mt-1" value={newName} onChange={(e) => setNewName(e.target.value)} />
            <div className="mt-4 flex justify-end gap-2">
              <button className="btn-secondary" onClick={() => setDeployTarget(null)}>Cancel</button>
              <button className="btn-primary" disabled={busy || !newName.trim()} onClick={handleDeploy}>Deploy</button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title={`Delete the template '${pendingDelete?.nom}'?`}
        message="The template's disk will be permanently deleted."
        confirmLabel="Delete"
        onCancel={() => setPendingDelete(null)}
        onConfirm={handleDelete}
      />
    </div>
  );
}
