import { useEffect, useState } from "react";
import { CalendarClock, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { fetchAllBackups } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { backupModeLabel } from "../../lib/labels";

function formatSize(bytes) {
  if (!bytes) return "--";
  const go = bytes / (1024 ** 3);
  return go >= 1 ? `${go.toFixed(2)} GB` : `${(bytes / (1024 ** 2)).toFixed(0)} MB`;
}

// Real: GET /backups (all VMs together, see app/routers/backups.py).
export default function BackupsTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);

  useEffect(() => {
    fetchAllBackups().then(setRows).catch((e) => pushToast({ kind: "error", title: "Backups error", message: e.message }));
  }, [pushToast]);

  if (rows == null) return <div className="card p-4 text-sm text-anthracite-400">Loading...</div>;

  if (rows.length === 0) {
    return (
      <div className="card flex flex-col items-center gap-2 p-8 text-center">
        <CalendarClock size={26} className="text-anthracite-400" />
        <p className="text-sm text-anthracite-300">No backups yet.</p>
        <p className="text-xs text-anthracite-400 max-w-sm">
          Start a manual backup or schedule one from the Backup tab of a VM.
        </p>
      </div>
    );
  }

  return (
    <div className="card divide-y divide-anthracite-600 max-h-[70vh] overflow-y-auto" tabIndex={0} role="region" aria-label="Backups">
      <div className="grid grid-cols-[110px_1fr_80px_90px_1fr_50px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400 sticky top-0 bg-anthracite-800">
        <span>Time</span><span>VM</span><span>Mode</span><span>Size</span><span>Location / cause</span><span>Status</span>
      </div>
      {rows.map((b) => (
        <div key={b.id} className="grid grid-cols-[110px_1fr_80px_90px_1fr_50px] gap-2 px-4 py-2 text-sm items-center">
          <span className="text-anthracite-400 text-xs font-mono">{new Date(b.cree_le).toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
          <span className="text-anthracite-100 truncate">{b.vm_name}</span>
          <span className="text-xs text-anthracite-400">{backupModeLabel(b.mode)}</span>
          <span className="text-xs text-anthracite-400">{formatSize(b.taille_octets)}</span>
          <span className="text-anthracite-400 text-xs truncate" title={b.erreur || b.chemin}>{b.erreur || b.chemin}</span>
          <span>
            {b.statut === "termine" ? <CheckCircle2 size={14} className="text-status-running" />
              : b.statut === "echec" ? <XCircle size={14} className="text-status-error" />
              : <Loader2 size={14} className="animate-spin text-accent-blue" />}
          </span>
        </div>
      ))}
    </div>
  );
}
