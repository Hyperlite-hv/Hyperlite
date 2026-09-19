import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import StatusBadge from "../../components/StatusBadge";
import { fetchTasks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";

// The same persisted `tasks` table as NodeTasksTab.jsx (GET /tasks, see
// app/core/tasks.py) but without a per-node filter: "Recent activity" at the
// Datacenter level, all machines/containers together, Hyperlite's equivalent of
// the "Recent Tasks" panel of a vSphere/Proxmox dashboard.
const TASK_LABELS = {
  create_vm: "Create VM", delete_vm: "Delete VM", start_vm: "Start VM",
  stop_vm: "Stop VM", force_stop_vm: "Force stop VM", restart_vm: "Restart VM",
  clone_vm: "Clone VM", migrate_vm: "Migrate VM", auto_install: "Unattended installation",
  create_snapshot: "Create snapshot", delete_snapshot: "Delete snapshot", restore_snapshot: "Restore snapshot",
  backup_vm: "Back up VM", restore_backup: "Restore backup",
  export_vm: "Export VM", upload_vm_disk: "Upload disk",
  create_container: "Create container", upload_iso: "Upload ISO",
  host_shell: "Host shell", hyperlite_update: "Hyperlite update", run_job: "Automation job",
};

const STATUT_ETAT = { en_cours: "avertissement", termine: "actif", echec: "erreur", en_attente: "avertissement" };

function formatHeure(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDuree(debut, fin) {
  if (!debut) return "--";
  const endMs = fin ? new Date(fin).getTime() : Date.now();
  const s = Math.max(0, Math.round((endMs - new Date(debut).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function ActivityTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [statutFiltre, setStatutFiltre] = useState("");

  const load = useCallback(() => {
    fetchTasks({ statut: statutFiltre || undefined, limit: 200 })
      .then(setRows)
      .catch((e) => pushToast({ kind: "error", title: "Tasks error", message: e.message }));
  }, [statutFiltre, pushToast]);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select aria-label="Filter by status"
          value={statutFiltre}
          onChange={(e) => setStatutFiltre(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100"
        >
          <option value="">All statuses</option>
          <option value="en_cours">Running</option>
          <option value="termine">Completed</option>
          <option value="echec">Failed</option>
        </select>
        <button
          onClick={load}
          className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-anthracite-300 hover:text-anthracite-100 border border-anthracite-600 rounded-md"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div className="card divide-y divide-anthracite-600 max-h-[70vh] overflow-y-auto" tabIndex={0} role="region" aria-label="Recent activity">
        <div className="grid grid-cols-[150px_1fr_1fr_120px_110px_80px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400 sticky top-0 bg-anthracite-800">
          <span>Time</span><span>Task</span><span>Target</span><span>Node</span><span>User</span><span>Duration</span>
        </div>

        {rows == null && <div className="px-4 py-3 text-sm text-anthracite-400">Loading...</div>}
        {rows && rows.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No recent activity.</div>}

        {rows && rows.map((t) => (
          <div key={t.id} className="grid grid-cols-[150px_1fr_1fr_120px_110px_80px] gap-2 px-4 py-2 text-sm items-center">
            <span className="text-anthracite-400 text-xs font-mono">{formatHeure(t.cree_le)}</span>
            <span className="flex items-center gap-1.5 text-anthracite-100">
              <StatusBadge etat={STATUT_ETAT[t.statut]} showLabel={false} />
              {TASK_LABELS[t.type] || t.type}
            </span>
            <span className="text-anthracite-300 truncate" title={t.erreur || ""}>{t.cible || "--"}</span>
            <span className="text-anthracite-400 text-xs truncate">{t.node || "--"}</span>
            <span className="text-anthracite-400 text-xs truncate">{t.username || "--"}</span>
            <span className="text-anthracite-400 text-xs">{formatDuree(t.debut_le, t.fin_le)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
