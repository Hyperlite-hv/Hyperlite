import { useState } from "react";
import { ChevronUp, ChevronDown, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import ProgressBar from "../components/ProgressBar";
import { useInfraStore } from "../store/useInfraStore";
import { statusLabel } from "../lib/labels";
import { Badge } from "@/components/ui/badge";

const TASK_LABELS = {
  start_vm: "Start VM", stop_vm: "Stop VM", restart_vm: "Restart VM",
  delete_vm: "Delete VM", create_vm: "Create VM", migrate_vm: "Migrer VM", create_snapshot: "Create snapshot",
  upload_iso: "Upload ISO",
};

function formatDuration(debut, fin) {
  const end = fin || Date.now();
  const s = Math.round((end - debut) / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function StatusIcon({ statut }) {
  if (statut === "en_cours") return <Loader2 size={14} className="text-accent-blue animate-spin" />;
  if (statut === "echec") return <XCircle size={14} className="text-status-error" />;
  return <CheckCircle2 size={14} className="text-status-running" />;
}

export default function TaskLogPanel() {
  const tasks = useInfraStore((s) => s.tasks);
  const collapsed = useInfraStore((s) => s.taskLogCollapsed);
  const toggle = useInfraStore((s) => s.toggleTaskLog);
  const [openId, setOpenId] = useState(null);

  const runningCount = tasks.filter((t) => t.statut === "en_cours").length;

  return (
    <div className={`shrink-0 border-t border-border bg-card flex flex-col ${collapsed ? "h-9" : "h-52"} transition-[height] duration-200`}>
      <button
        onClick={toggle}
        className="flex h-9 shrink-0 items-center gap-2 px-3 text-xs font-medium text-foreground/80 transition-colors duration-150 hover:text-foreground"
      >
        {collapsed ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        Tasks
        {runningCount > 0 && <Badge variant="secondary" className="bg-accent-blue/20 text-accent-blue">{runningCount} running</Badge>}
        <span className="ml-auto text-muted-foreground">{tasks.length} in total</span>
      </button>

      {!collapsed && (
        <div className="flex-1 overflow-y-auto px-2 pb-2 animate-in fade-in-0 duration-150" tabIndex={0} role="region" aria-label="Task list">
          {tasks.length === 0 && <div className="px-2 py-4 text-sm text-muted-foreground">No tasks yet.</div>}
          {tasks.map((t) => (
            <div key={t.id} className="rounded-md transition-colors duration-150 hover:bg-muted/60">
              <button
                className="flex w-full items-center gap-2.5 px-2 py-1.5 text-left text-sm"
                onClick={() => setOpenId((id) => (id === t.id ? null : t.id))}
              >
                <StatusIcon statut={t.statut} />
                <span className="text-foreground w-36 truncate">{TASK_LABELS[t.type] || t.type}</span>
                <span className="text-foreground/80 flex-1 truncate">{t.cible}</span>
                {t.statut === "en_cours" && <span className="w-28"><ProgressBar value={t.progres} size="sm" /></span>}
                <span className="text-muted-foreground text-xs w-24 text-right">{formatDuration(t.debut, t.fin)}</span>
              </button>
              {openId === t.id && (
                <div className="px-8 pb-2 text-xs text-muted-foreground space-y-0.5 animate-in fade-in-0 duration-150">
                  <div>Node: {t.node}</div>
                  <div>User: {t.utilisateur}</div>
                  <div>Status: {statusLabel(t.statut)}{t.erreur ? ` -- ${t.erreur}` : ""}</div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
