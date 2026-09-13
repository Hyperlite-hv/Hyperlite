import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, RotateCcw, Trash2, AlertTriangle } from "lucide-react";
import ConfirmDialog from "../../components/ConfirmDialog";
import ProgressBar from "../../components/ProgressBar";
import { fetchSnapshots, createSnapshot, restoreSnapshot, deleteSnapshot, fetchTaskDetail } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";

// Reel : GET/POST /vms/{name}/snapshots + POST .../restore?confirm=true +
// DELETE .../{snapshot_name}. Reecrit le 2026-09-13 (chantier 4) :
// - create/restore repondent maintenant 202 + un task_id (l'operation tourne
//   en arriere-plan cote backend, potentiellement plusieurs secondes le temps
//   de (de)serialiser la memoire de la VM) -- on suit la vraie tache via
//   GET /tasks/{id} (voir api/client.js, chantier 1) au lieu de bloquer sur
//   le fetch ou d'inventer un pourcentage que libvirt n'expose pas.
// - `parent` est un vrai champ renvoye par le backend (getParent()), pas
//   invente cote front : sert a indenter l'arbre de snapshots imbriques.
function formatElapsed(startedAt) {
  const s = Math.round((Date.now() - startedAt) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function VMSnapshotsTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [snapshots, setSnapshots] = useState(null);
  const [pending, setPending] = useState(null); // { action: "restore"|"delete", snap }
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState(null); // { label, startedAt } pendant un create/restore en cours
  const [, forceTick] = useState(0);

  const reload = useCallback(async () => {
    try { setSnapshots(await fetchSnapshots(vm.nom)); }
    catch (e) { pushToast({ kind: "error", title: "Erreur snapshots", message: e.message }); }
  }, [vm?.nom, pushToast]);

  useEffect(() => { if (vm?.nom) reload(); }, [vm?.nom, reload]);

  // Rafraichit juste le compteur "Xs écoulées" pendant qu'un job tourne.
  useEffect(() => {
    if (!job) return;
    const id = setInterval(() => forceTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [job]);

  const waitTask = useCallback(async (taskId) => {
    for (;;) {
      const t = await fetchTaskDetail(taskId);
      if (t.statut !== "en_cours") return t;
      await new Promise((r) => setTimeout(r, 1000));
    }
  }, []);

  if (!vm) return null;
  if (snapshots == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  async function handleCreate() {
    setBusy(true);
    const name = `snap-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}`;
    try {
      const { task_id } = await createSnapshot(vm.nom, name, "Créé depuis le dashboard");
      setJob({ label: `Création de « ${name} »`, startedAt: Date.now() });
      const t = await waitTask(task_id);
      if (t.statut === "termine") {
        pushToast({ kind: "success", title: "Snapshot créé", message: name });
      } else {
        pushToast({ kind: "error", title: "Échec de la création", message: t.erreur || "Erreur inconnue" });
      }
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la création", message: e.message });
    } finally { setBusy(false); setJob(null); }
  }

  async function confirmAction() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.action === "delete") {
        await deleteSnapshot(vm.nom, pending.snap.nom);
        pushToast({ kind: "success", title: "Snapshot supprimé", message: pending.snap.nom });
        await reload();
      } else {
        const { task_id } = await restoreSnapshot(vm.nom, pending.snap.nom);
        setJob({ label: `Restauration vers « ${pending.snap.nom} »`, startedAt: Date.now() });
        const t = await waitTask(task_id);
        if (t.statut === "termine") {
          pushToast({ kind: "success", title: "Snapshot restauré", message: pending.snap.nom });
        } else {
          pushToast({ kind: "error", title: "Échec de la restauration", message: t.erreur || "Erreur inconnue" });
        }
        await reload();
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Echec", message: e.message });
    } finally {
      setBusy(false);
      setJob(null);
      setPending(null);
    }
  }

  // Profondeur d'indentation d'un snapshot dans l'arbre (via `parent`,
  // remonte jusqu'a la racine) -- affichage simple, pas de vraie vue graphe.
  const depthOf = (snap, seen = new Set()) => {
    if (!snap.parent || seen.has(snap.nom)) return 0;
    seen.add(snap.nom);
    const parent = snapshots.find((s) => s.nom === snap.parent);
    return parent ? 1 + depthOf(parent, seen) : 0;
  };

  return (
    <div className="space-y-3">
      {snapshots.length >= 3 && (
        <div className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2">
          <AlertTriangle size={15} className="text-status-warning shrink-0 mt-0.5" />
          <p className="text-xs text-anthracite-200">
            {snapshots.length} snapshots actifs sur cette VM. Chaque snapshot conservé ralentit le disque et fait grossir le
            fichier qcow2 — supprimez ceux qui ne sont plus utiles dès que possible.
          </p>
        </div>
      )}

      {isAdmin && (
        <button className="btn-primary" disabled={busy} onClick={handleCreate}>
          <Camera size={14} /> Créer un snapshot
        </button>
      )}

      {job && (
        <div className="card p-3 space-y-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="text-anthracite-100">{job.label}</span>
            <span className="text-xs text-anthracite-400">{formatElapsed(job.startedAt)} écoulées</span>
          </div>
          <ProgressBar indeterminate statut="en_cours" />
          <p className="text-[11px] text-anthracite-500">
            Peut prendre plusieurs secondes si la VM tourne (la mémoire est incluse automatiquement).
          </p>
        </div>
      )}

      <div className="card divide-y divide-anthracite-600">
        {snapshots.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucun snapshot.</div>}
        {snapshots.map((s) => (
          <div key={s.nom} className="flex items-center gap-3 px-4 py-2.5" style={{ paddingLeft: `${16 + depthOf(s) * 20}px` }}>
            <Camera size={14} className="text-anthracite-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-sm text-anthracite-100">
                {s.nom} {s.actuel && <span className="ml-1 rounded bg-accent-blue/20 px-1.5 py-0.5 text-[10px] text-accent-blue">actuel</span>}
              </div>
              <div className="text-xs text-anthracite-400 truncate">
                {s.description || "--"} {s.date_creation ? `-- ${s.date_creation}` : ""}
                {s.etat_vm && ` -- VM ${s.etat_vm === "running" ? "en marche (mémoire incluse)" : "arrêtée (disque seul)"}`}
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
        message={pending?.action === "delete" ? "Cette action est irréversible." : "L'état actuel de la VM sera remplacé par celui du snapshot."}
        confirmLabel={pending?.action === "delete" ? "Supprimer" : "Restaurer"}
        danger={pending?.action === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
