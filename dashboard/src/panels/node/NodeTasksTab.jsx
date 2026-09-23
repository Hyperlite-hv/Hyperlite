import LoadingState from "../../components/LoadingState";
import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronUp, ChevronsUpDown, RefreshCw } from "lucide-react";
import StatusBadge from "../../components/StatusBadge";
import { fetchTasks, fetchTaskDetail } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

const TASK_LABELS = {
  start_vm: "Start VM", stop_vm: "Stop VM", force_stop_vm: "Force stop VM",
  restart_vm: "Restart VM", delete_vm: "Delete VM", create_vm: "Create VM",
  update_vm: "Change resources", create_snapshot: "Create snapshot",
  upload_iso: "Upload ISO",
};

const STATUT_ETAT = { en_cours: "avertissement", termine: "actif", echec: "erreur", en_attente: "avertissement" };
const STATUT_LABEL = { en_cours: "Running", termine: "Completed", echec: "Failed", en_attente: "Pending" };

const COLUMNS = [
  { key: "cree_le", label: "Time" },
  { key: "cible", label: "Target" },
  { key: "username", label: "User" },
  { key: "type", label: "Task" },
  { key: "statut", label: "Status" },
];

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

// vCenter-style "Recent Tasks" view. Source of truth = the `tasks` table persisted
// on the backend (GET /tasks), not the ephemeral tasks of the store (those only
// survive the current session, see TaskLogPanel for the real-time ticker). Here we
// want the real history, shared between users/tabs, with distinct creation/start/
// end times.
export default function NodeTasksTab({ resource: node }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [tri, setTri] = useState("cree_le");
  const [ordre, setOrdre] = useState("desc");
  const [statutFiltre, setStatutFiltre] = useState("all");
  const [recherche, setRecherche] = useState("");
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState(null);

  const load = useCallback(() => {
    if (!node) return;
    fetchTasks({ node: node.id, statut: statutFiltre === "all" ? undefined : statutFiltre, cible: recherche || undefined, tri, ordre, limit: 300 })
      .then(setRows)
      .catch((e) => pushToast({ kind: "error", title: "Tasks error", message: e.message }));
  }, [node, statutFiltre, recherche, tri, ordre, pushToast]);

  useEffect(() => {
    load();
    // Light refresh to follow running tasks without the user having to reload the
    // page, in the same spirit as the silent polling of useInfraStore.refreshAll().
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!openId) { setDetail(null); return; }
    fetchTaskDetail(openId)
      .then(setDetail)
      .catch((e) => pushToast({ kind: "error", title: "Task detail error", message: e.message }));
  }, [openId, pushToast]);

  const toggleTri = (key) => {
    if (tri === key) { setOrdre((o) => (o === "asc" ? "desc" : "asc")); return; }
    setTri(key); setOrdre("desc");
  };

  if (!node) return null;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <NativeSelect aria-label="Filter by status" className="w-auto" value={statutFiltre} onChange={(e) => setStatutFiltre(e.target.value)}>
          <option value="all">All statuses</option>
          <option value="en_cours">Running</option>
          <option value="termine">Completed</option>
          <option value="echec">Failed</option>
        </NativeSelect>
        <Input aria-label="Filter by target..."
          type="text"
          placeholder="Filter by target..."
          value={recherche}
          onChange={(e) => setRecherche(e.target.value)}
          className="flex-1 min-w-[160px]"
        />
        <Button variant="outline" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      <Card className="p-0 divide-y divide-border max-h-[65vh] overflow-y-auto" tabIndex={0} role="region" aria-label="Tasks">
        <div className="grid grid-cols-[150px_1fr_120px_160px_110px_80px] gap-2 px-4 py-2 text-xs font-medium text-muted-foreground sticky top-0 bg-card">
          {COLUMNS.map((c) => (
            <button key={c.key} onClick={() => toggleTri(c.key)} className="flex items-center gap-1 text-left transition-colors duration-150 hover:text-foreground">
              {c.label}
              {tri === c.key ? (ordre === "asc" ? <ChevronUp size={12} /> : <ChevronDown size={12} />) : <ChevronsUpDown size={12} className="opacity-40" />}
            </button>
          ))}
          <span>Duration</span>
        </div>

        {rows == null && <div className="px-4 py-3 text-sm text-muted-foreground"><LoadingState /></div>}
        {rows && rows.length === 0 && <div className="px-4 py-3 text-sm text-muted-foreground">No tasks on this node.</div>}

        {rows && rows.map((t) => (
          <div key={t.id}>
            <button
              onClick={() => setOpenId((id) => (id === t.id ? null : t.id))}
              className="w-full grid grid-cols-[150px_1fr_120px_160px_110px_80px] gap-2 px-4 py-2 text-sm items-center text-left transition-colors duration-150 hover:bg-muted/50"
            >
              <span className="text-muted-foreground text-xs font-mono">{formatHeure(t.cree_le)}</span>
              <span className="text-foreground truncate">{t.cible || "--"}</span>
              <span className="text-foreground/80 truncate">{t.username || "--"}</span>
              <span className="text-foreground/90">{TASK_LABELS[t.type] || t.type}</span>
              <StatusBadge etat={STATUT_ETAT[t.statut]} showLabel={false} />
              <span className="text-muted-foreground text-xs">{formatDuree(t.debut_le, t.fin_le)}</span>
            </button>

            {openId === t.id && (
              <div className="px-8 pb-3 text-xs text-muted-foreground space-y-1 bg-muted/30 animate-in fade-in-0 duration-150">
                <div>Status: <span className="text-foreground/90">{STATUT_LABEL[t.statut] || t.statut}</span></div>
                <div>Created on: {formatHeure(t.cree_le)}</div>
                <div>Started on: {formatHeure(t.debut_le)}</div>
                <div>Finished on: {formatHeure(t.fin_le)}</div>
                <div>Total duration: {formatDuree(t.debut_le, t.fin_le)}</div>
                {t.erreur && <div className="text-status-error">Cause of failure: {t.erreur}</div>}
                {detail && detail.id === t.id && detail.logs?.length > 0 && (
                  <div className="pt-1">
                    <div className="text-muted-foreground mb-0.5">Journal entries for this target:</div>
                    {detail.logs.slice(0, 5).map((l, i) => (
                      <div key={i} className="font-mono">
                        {formatHeure(l.timestamp)} — {l.action} : {l.result}{l.error_message ? ` (${l.error_message})` : ""}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}
