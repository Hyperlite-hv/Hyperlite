import { useCallback, useEffect, useState } from "react";
import { Camera, RotateCcw, Trash2 } from "lucide-react";
import ConfirmDialog from "../../components/ConfirmDialog";
import { fetchSnapshots, createSnapshot, restoreSnapshot, deleteSnapshot } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";

// Reel : GET/POST /vms/{name}/snapshots + POST .../restore?confirm=true +
// DELETE .../{snapshot_name}, deja fonctionnels cote backend. Pas de notion
// d'arbre imbrique cote backend (liste plate) -- contrairement a la version
// mock precedente qui inventait un champ `parent` pour illustrer ce rendu.
export default function VMSnapshotsTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [snapshots, setSnapshots] = useState(null);
  const [pending, setPending] = useState(null); // { action: "restore"|"delete", snap }
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try { setSnapshots(await fetchSnapshots(vm.nom)); }
    catch (e) { pushToast({ kind: "error", title: "Erreur snapshots", message: e.message }); }
  }, [vm?.nom, pushToast]);

  useEffect(() => { if (vm?.nom) reload(); }, [vm?.nom, reload]);

  if (!vm) return null;
  if (snapshots == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  async function handleCreate() {
    setBusy(true);
    const name = `snap-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}`;
    try {
      await createSnapshot(vm.nom, name, "Cree depuis le dashboard");
      pushToast({ kind: "success", title: "Snapshot cree", message: name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Echec de la creation", message: e.message });
    } finally { setBusy(false); }
  }

  async function confirmAction() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.action === "delete") {
        await deleteSnapshot(vm.nom, pending.snap.nom);
        pushToast({ kind: "success", title: "Snapshot supprime", message: pending.snap.nom });
      } else {
        await restoreSnapshot(vm.nom, pending.snap.nom);
        pushToast({ kind: "success", title: "Snapshot restaure", message: pending.snap.nom });
      }
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Echec", message: e.message });
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  return (
    <div className="space-y-3">
      {isAdmin && (
        <button className="btn-primary" disabled={busy} onClick={handleCreate}>
          <Camera size={14} /> Creer un snapshot
        </button>
      )}
      <div className="card divide-y divide-anthracite-600">
        {snapshots.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucun snapshot.</div>}
        {snapshots.map((s) => (
          <div key={s.nom} className="flex items-center gap-3 px-4 py-2.5">
            <Camera size={14} className="text-anthracite-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-sm text-anthracite-100">
                {s.nom} {s.actuel && <span className="ml-1 rounded bg-accent-blue/20 px-1.5 py-0.5 text-[10px] text-accent-blue">actuel</span>}
              </div>
              <div className="text-xs text-anthracite-400 truncate">
                {s.description || "--"} {s.date_creation ? `-- ${s.date_creation}` : ""}
              </div>
            </div>
            {isAdmin && (
              <>
                <button className="btn-secondary" disabled={busy} onClick={() => setPending({ action: "restore", snap: s })}><RotateCcw size={13} /> Restaurer</button>
                <button className="btn-danger" disabled={busy} onClick={() => setPending({ action: "delete", snap: s })}><Trash2 size={13} /></button>
              </>
            )}
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={!!pending}
        title={pending?.action === "delete" ? `Supprimer '${pending.snap.nom}' ?` : `Restaurer '${pending?.snap.nom}' ?`}
        message={pending?.action === "delete" ? "Cette action est irreversible." : "L'etat actuel de la VM sera remplace par celui du snapshot."}
        confirmLabel={pending?.action === "delete" ? "Supprimer" : "Restaurer"}
        danger={pending?.action === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
