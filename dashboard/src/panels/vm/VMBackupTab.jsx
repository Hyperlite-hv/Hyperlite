import { confirmAction as askConfirm } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Save, Play, Trash2, RotateCcw } from "lucide-react";
import ConfirmDialog from "../../components/ConfirmDialog";
import {
  fetchVMBackups, createBackup, deleteBackup, restoreBackup,
  fetchBackupSchedule, setBackupSchedule, deleteBackupSchedule,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { backupModeLabel, frequencyLabel } from "../../lib/labels";

function formatSize(bytes) {
  if (!bytes) return "--";
  const go = bytes / (1024 ** 3);
  return go >= 1 ? `${go.toFixed(2)} GB` : `${(bytes / (1024 ** 2)).toFixed(0)} MB`;
}

// Real: GET/POST /vms/{name}/backups, GET/PUT/DELETE .../backup-schedule
// (see app/routers/backups.py). Cold backup (stopped VM, plain copy) or hot
// backup (running VM, transient external snapshot + copy + merge, with no
// service interruption).
export default function VMBackupTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [backups, setBackups] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [form, setForm] = useState({ frequence: "quotidien", heure: "02:00", retention_count: 7 });
  const [pending, setPending] = useState(null); // { action, backup }
  const [busy, setBusy] = useState(false);

  const vmName = vm?.nom;
  const reload = useCallback(() => {
    if (!vmName) return;
    fetchVMBackups(vmName).then(setBackups).catch((e) => pushToast({ kind: "error", title: "Backups error", message: e.message }));
    fetchBackupSchedule(vmName).then((s) => { setSchedule(s); if (s) setForm(s); }).catch(() => {});
  }, [vmName, pushToast]);

  useEffect(() => { reload(); }, [reload]);

  if (!vm) return null;

  async function handleBackupNow() {
    setBusy(true);
    try {
      await createBackup(vm.nom);
      pushToast({ kind: "success", title: "Backup started", message: `${vm.nom}: see the Tasks tab for progress` });
      setTimeout(reload, 3000);
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleSaveSchedule() {
    setBusy(true);
    try {
      const s = await setBackupSchedule(vm.nom, form);
      setSchedule(s);
      pushToast({ kind: "success", title: "Schedule saved", message: `${frequencyLabel(form.frequence)} at ${form.heure}` });
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDeleteSchedule() {
    if (!(await askConfirm({ title: "Delete the backup schedule?", message: `Scheduled backups of ${vm.nom} stop. Existing backups are kept.`, confirmLabel: "Delete" }))) return;
    setBusy(true);
    try {
      await deleteBackupSchedule(vm.nom);
      setSchedule(null);
      pushToast({ kind: "success", title: "Schedule deleted", message: vm.nom });
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function confirmAction() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.action === "delete") {
        await deleteBackup(pending.backup.id);
        pushToast({ kind: "success", title: "Backup deleted", message: `#${pending.backup.id}` });
      } else if (pending.action === "restore-overwrite") {
        await restoreBackup(pending.backup.id, "overwrite");
        pushToast({ kind: "success", title: "Restore started", message: "See the Tasks tab" });
      }
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); setPending(null); }
  }

  async function handleRestoreNew(backup) {
    const newName = window.prompt(`Name of the new VM restored from backup #${backup.id}:`, `${vm.nom}-restored`);
    if (!newName || !newName.trim()) return;
    try {
      await restoreBackup(backup.id, "new", newName.trim());
      pushToast({ kind: "success", title: "Restore started", message: `New VM: ${newName.trim()}` });
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  return (
    <div className="space-y-4">
      {isAdmin && (
        <button className="btn-primary" disabled={busy} onClick={handleBackupNow}>
          <Play size={14} /> Back up now
        </button>
      )}

      <div className="card">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-anthracite-600">
          <CalendarClock size={15} className="text-anthracite-400" />
          <h3 className="text-sm font-semibold text-anthracite-100">Scheduled backup</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2 p-4">
          <select aria-label="Backup frequency" className="input" disabled={!isAdmin} value={form.frequence} onChange={(e) => setForm((f) => ({ ...f, frequence: e.target.value }))}>
            <option value="quotidien">Daily</option>
            <option value="hebdomadaire">Weekly (Monday)</option>
            <option value="mensuel">Monthly</option>
          </select>
          <input aria-label="Backup time" type="time" className="input" disabled={!isAdmin} value={form.heure} onChange={(e) => setForm((f) => ({ ...f, heure: e.target.value }))} />
          <label className="text-xs text-anthracite-400 flex items-center gap-1.5">
            Retention
            <input type="number" min={1} max={365} className="input w-20" disabled={!isAdmin}
              value={form.retention_count} onChange={(e) => setForm((f) => ({ ...f, retention_count: Number(e.target.value) }))} />
            backups
          </label>
          {isAdmin && (
            <div className="ml-auto flex gap-2">
              {schedule && <button className="btn-secondary" disabled={busy} onClick={handleDeleteSchedule}>Disable</button>}
              <button className="btn-primary" disabled={busy} onClick={handleSaveSchedule}><Save size={13} /> Save</button>
            </div>
          )}
        </div>
        {schedule && (
          <div className="px-4 pb-3 text-xs text-anthracite-400">
            Next run: {schedule.prochaine_execution ? new Date(schedule.prochaine_execution).toLocaleString(undefined) : "--"}
            {schedule.derniere_execution && ` — last: ${new Date(schedule.derniere_execution).toLocaleString(undefined)}`}
          </div>
        )}
      </div>

      <div className="card divide-y divide-anthracite-600">
        {backups == null && <div className="px-4 py-3 text-sm text-anthracite-400">Loading...</div>}
        {backups && backups.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No backups.</div>}
        {backups && backups.map((b) => (
          <div key={b.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <span className={`h-2 w-2 rounded-full shrink-0 ${b.statut === "termine" ? "bg-status-running" : b.statut === "echec" ? "bg-status-error" : "bg-status-warning animate-pulse"}`} />
            <span className="text-anthracite-100">{new Date(b.cree_le).toLocaleString(undefined)}</span>
            <span className="text-xs text-anthracite-400">{backupModeLabel(b.mode)}</span>
            <span className="text-xs text-anthracite-400">{formatSize(b.taille_octets)}</span>
            {b.erreur && <span className="text-xs text-status-error truncate" title={b.erreur}>{b.erreur}</span>}
            {isAdmin && b.statut === "termine" && (
              <div className="ml-auto flex gap-1.5">
                <button className="btn-secondary" onClick={() => setPending({ action: "restore-overwrite", backup: b })} title="Restore over the original VM">
                  <RotateCcw size={13} /> In place
                </button>
                <button className="btn-secondary" onClick={() => handleRestoreNew(b)} title="Restore to a new VM">
                  New VM
                </button>
                <button aria-label={`Delete backup #${b.id}`} className="btn-danger" onClick={() => setPending({ action: "delete", backup: b })}><Trash2 size={13} /></button>
              </div>
            )}
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={!!pending}
        title={pending?.action === "delete" ? `Delete backup #${pending.backup.id}?` : `Restore over '${vm.nom}'?`}
        message={pending?.action === "delete" ? "This action is irreversible." : "The VM must be stopped. Its current disk will be replaced by the one from the backup."}
        confirmLabel={pending?.action === "delete" ? "Delete" : "Restore"}
        danger={pending?.action === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
