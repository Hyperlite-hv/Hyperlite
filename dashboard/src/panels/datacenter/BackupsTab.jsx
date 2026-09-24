import LoadingState from "../../components/LoadingState";
import { useEffect, useState } from "react";
import { CalendarClock, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { fetchAllBackups } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { backupModeLabel } from "../../lib/labels";
import { Card } from "@/components/ui/card";

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

  if (rows == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  if (rows.length === 0) {
    return (
      <Card className="flex flex-col items-center gap-2 p-8 text-center">
        <CalendarClock size={26} className="text-muted-foreground" />
        <p className="text-sm text-foreground/80">No backups yet.</p>
        <p className="text-xs text-muted-foreground max-w-sm">
          Start a manual backup or schedule one from the Backup tab of a VM.
        </p>
      </Card>
    );
  }

  return (
    <Card className="p-0 divide-y divide-border max-h-[70vh] overflow-y-auto" tabIndex={0} role="region" aria-label="Backups">
      <div className="grid grid-cols-[110px_1fr_80px_90px_1fr_50px] gap-2 px-4 py-2 text-xs font-medium text-muted-foreground sticky top-0 bg-card">
        <span>Time</span><span>VM</span><span>Mode</span><span>Size</span><span>Location / cause</span><span>Status</span>
      </div>
      {rows.map((b) => (
        <div key={b.id} className="grid grid-cols-[110px_1fr_80px_90px_1fr_50px] gap-2 px-4 py-2 text-sm items-center transition-colors duration-150 hover:bg-muted/40">
          <span className="text-muted-foreground text-xs font-mono">{new Date(b.cree_le).toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
          <span className="text-foreground truncate">{b.vm_name}</span>
          <span className="text-xs text-muted-foreground">{backupModeLabel(b.mode)}</span>
          <span className="text-xs text-muted-foreground">{formatSize(b.taille_octets)}</span>
          <span className="text-muted-foreground text-xs truncate" title={b.erreur || b.chemin}>{b.erreur || b.chemin}</span>
          <span>
            {b.statut === "termine" ? <CheckCircle2 size={14} className="text-status-running" />
              : b.statut === "echec" ? <XCircle size={14} className="text-status-error" />
              : <Loader2 size={14} className="animate-spin text-accent-blue" />}
          </span>
        </div>
      ))}
    </Card>
  );
}
