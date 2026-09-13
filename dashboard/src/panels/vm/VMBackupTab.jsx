import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Save, Play, Trash2, RotateCcw } from "lucide-react";
import ConfirmDialog from "../../components/ConfirmDialog";
import {
  fetchVMBackups, createBackup, deleteBackup, restoreBackup,
  fetchBackupSchedule, setBackupSchedule, deleteBackupSchedule,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";

function formatSize(bytes) {
  if (!bytes) return "--";
  const go = bytes / (1024 ** 3);
  return go >= 1 ? `${go.toFixed(2)} Go` : `${(bytes / (1024 ** 2)).toFixed(0)} Mo`;
}

// Reel : GET/POST /vms/{name}/backups, GET/PUT/DELETE .../backup-schedule
// (voir app/routers/backups.py, chantier 13). Backup a froid (VM arretee,
// simple copie) ou a chaud (VM active, snapshot externe transitoire +
// copie + fusion -- aucune interruption de service).
export default function VMBackupTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [backups, setBackups] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [form, setForm] = useState({ frequence: "quotidien", heure: "02:00", retention_count: 7 });
  const [pending, setPending] = useState(null); // { action, backup }
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    if (!vm?.nom) return;
    fetchVMBackups(vm.nom).then(setBackups).catch((e) => pushToast({ kind: "error", title: "Erreur backups", message: e.message }));
    fetchBackupSchedule(vm.nom).then((s) => { setSchedule(s); if (s) setForm(s); }).catch(() => {});
  }, [vm?.nom, pushToast]);

  useEffect(() => { reload(); }, [reload]);

  if (!vm) return null;

  async function handleBackupNow() {
    setBusy(true);
    try {
      await createBackup(vm.nom);
      pushToast({ kind: "success", title: "Sauvegarde lancée", message: `${vm.nom} — voir l'onglet Tâches pour la progression` });
      setTimeout(reload, 3000);
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleSaveSchedule() {
    setBusy(true);
    try {
      const s = await setBackupSchedule(vm.nom, form);
      setSchedule(s);
      pushToast({ kind: "success", title: "Planification enregistrée", message: `${form.frequence} à ${form.heure}` });
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDeleteSchedule() {
    setBusy(true);
    try {
      await deleteBackupSchedule(vm.nom);
      setSchedule(null);
      pushToast({ kind: "success", title: "Planification supprimée", message: vm.nom });
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  async function confirmAction() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.action === "delete") {
        await deleteBackup(pending.backup.id);
        pushToast({ kind: "success", title: "Sauvegarde supprimée", message: `#${pending.backup.id}` });
      } else if (pending.action === "restore-overwrite") {
        await restoreBackup(pending.backup.id, "overwrite");
        pushToast({ kind: "success", title: "Restauration lancée", message: "Voir l'onglet Tâches" });
      }
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); setPending(null); }
  }

  async function handleRestoreNew(backup) {
    const newName = window.prompt(`Nom de la nouvelle VM restaurée depuis la sauvegarde #${backup.id} :`, `${vm.nom}-restaure`);
    if (!newName || !newName.trim()) return;
    try {
      await restoreBackup(backup.id, "new", newName.trim());
      pushToast({ kind: "success", title: "Restauration lancée", message: `Nouvelle VM : ${newName.trim()}` });
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    }
  }

  return (
    <div className="space-y-4">
      {isAdmin && (
        <button className="btn-primary" disabled={busy} onClick={handleBackupNow}>
          <Play size={14} /> Sauvegarder maintenant
        </button>
      )}

      <div className="card">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-anthracite-600">
          <CalendarClock size={15} className="text-anthracite-400" />
          <h3 className="text-sm font-semibold text-anthracite-100">Sauvegarde planifiée</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2 p-4">
          <select className="input" disabled={!isAdmin} value={form.frequence} onChange={(e) => setForm((f) => ({ ...f, frequence: e.target.value }))}>
            <option value="quotidien">Quotidienne</option>
            <option value="hebdomadaire">Hebdomadaire (lundi)</option>
            <option value="mensuel">Mensuelle</option>
          </select>
          <input type="time" className="input" disabled={!isAdmin} value={form.heure} onChange={(e) => setForm((f) => ({ ...f, heure: e.target.value }))} />
          <label className="text-xs text-anthracite-400 flex items-center gap-1.5">
            Rétention
            <input type="number" min={1} max={365} className="input w-20" disabled={!isAdmin}
              value={form.retention_count} onChange={(e) => setForm((f) => ({ ...f, retention_count: Number(e.target.value) }))} />
            sauvegardes
          </label>
          {isAdmin && (
            <div className="ml-auto flex gap-2">
              {schedule && <button className="btn-secondary" disabled={busy} onClick={handleDeleteSchedule}>Désactiver</button>}
              <button className="btn-primary" disabled={busy} onClick={handleSaveSchedule}><Save size={13} /> Enregistrer</button>
            </div>
          )}
        </div>
        {schedule && (
          <div className="px-4 pb-3 text-xs text-anthracite-400">
            Prochaine exécution : {schedule.prochaine_execution ? new Date(schedule.prochaine_execution).toLocaleString("fr-FR") : "--"}
            {schedule.derniere_execution && ` — dernière : ${new Date(schedule.derniere_execution).toLocaleString("fr-FR")}`}
          </div>
        )}
      </div>

      <div className="card divide-y divide-anthracite-600">
        {backups == null && <div className="px-4 py-3 text-sm text-anthracite-400">Chargement...</div>}
        {backups && backups.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucune sauvegarde.</div>}
        {backups && backups.map((b) => (
          <div key={b.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <span className={`h-2 w-2 rounded-full shrink-0 ${b.statut === "termine" ? "bg-status-running" : b.statut === "echec" ? "bg-status-error" : "bg-status-warning animate-pulse"}`} />
            <span className="text-anthracite-100">{new Date(b.cree_le).toLocaleString("fr-FR")}</span>
            <span className="text-xs text-anthracite-400">{b.mode === "chaud" ? "à chaud" : "à froid"}</span>
            <span className="text-xs text-anthracite-400">{formatSize(b.taille_octets)}</span>
            {b.erreur && <span className="text-xs text-status-error truncate" title={b.erreur}>{b.erreur}</span>}
            {isAdmin && b.statut === "termine" && (
              <div className="ml-auto flex gap-1.5">
                <button className="btn-secondary" onClick={() => setPending({ action: "restore-overwrite", backup: b })} title="Restaurer par-dessus la VM d'origine">
                  <RotateCcw size={13} /> Sur place
                </button>
                <button className="btn-secondary" onClick={() => handleRestoreNew(b)} title="Restaurer vers une nouvelle VM">
                  Nouvelle VM
                </button>
                <button className="btn-danger" onClick={() => setPending({ action: "delete", backup: b })}><Trash2 size={13} /></button>
              </div>
            )}
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={!!pending}
        title={pending?.action === "delete" ? `Supprimer la sauvegarde #${pending.backup.id} ?` : `Restaurer par-dessus '${vm.nom}' ?`}
        message={pending?.action === "delete" ? "Cette action est irréversible." : "La VM doit être arrêtée. Son disque actuel sera remplacé par celui de la sauvegarde."}
        confirmLabel={pending?.action === "delete" ? "Supprimer" : "Restaurer"}
        danger={pending?.action === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
