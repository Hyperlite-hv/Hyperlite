import LoadingState from "../../components/LoadingState";
import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, XCircle, RefreshCw } from "lucide-react";
import { fetchAuditLog, fetchAuditActions } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";

// Real: GET /audit, which reads the audit_log table fed from the start by every
// backend endpoint (log_action() is called everywhere). Filters by status/type/
// user/target/date. The failure message is made actionable through
// app/core/error_messages.py.
export default function JournalTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [entries, setEntries] = useState(null);
  const [actions, setActions] = useState([]);
  const [resultFiltre, setResultFiltre] = useState("");
  const [actionFiltre, setActionFiltre] = useState("");
  const [usernameFiltre, setUsernameFiltre] = useState("");
  const [ressourceFiltre, setRessourceFiltre] = useState("");
  const [depuis, setDepuis] = useState("");

  const load = useCallback(() => {
    fetchAuditLog({
      limit: 300,
      result: resultFiltre || undefined,
      action: actionFiltre || undefined,
      username: usernameFiltre || undefined,
      resource: ressourceFiltre || undefined,
      depuis: depuis ? new Date(depuis).toISOString() : undefined,
    })
      .then(setEntries)
      .catch((e) => pushToast({ kind: "error", title: "Journal error", message: e.message }));
  }, [resultFiltre, actionFiltre, usernameFiltre, ressourceFiltre, depuis, pushToast]);

  useEffect(() => { fetchAuditActions().then(setActions).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select aria-label="Filter by result"
          value={resultFiltre}
          onChange={(e) => setResultFiltre(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100"
        >
          <option value="">All results</option>
          <option value="succes">Success</option>
          <option value="echec">Failure</option>
        </select>
        <select aria-label="Filter by action type"
          value={actionFiltre}
          onChange={(e) => setActionFiltre(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100"
        >
          <option value="">All action types</option>
          {actions.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <input aria-label="User..."
          type="text"
          placeholder="User..."
          value={usernameFiltre}
          onChange={(e) => setUsernameFiltre(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100 w-32"
        />
        <input aria-label="Target (resource)..."
          type="text"
          placeholder="Target (resource)..."
          value={ressourceFiltre}
          onChange={(e) => setRessourceFiltre(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100 flex-1 min-w-[140px]"
        />
        <input aria-label="Show entries from"
          type="datetime-local"
          value={depuis}
          onChange={(e) => setDepuis(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100"
        />
        <button
          onClick={load}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-anthracite-300 hover:text-anthracite-100 border border-anthracite-600 rounded-md"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {entries == null && <div className="card p-4 text-sm text-anthracite-400"><LoadingState /></div>}

      {entries && (
        <div className="card divide-y divide-anthracite-600 max-h-[65vh] overflow-y-auto" tabIndex={0} role="region" aria-label="Journal entries">
          <div className="grid grid-cols-[110px_100px_1fr_1fr_70px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400 sticky top-0 bg-anthracite-800">
            <span>Time</span><span>User</span><span>Action</span><span>Resource / cause</span><span>Result</span>
          </div>
          {entries.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No entries for these filters.</div>}
          {entries.map((e) => (
            <div key={e.id} className="grid grid-cols-[110px_100px_1fr_1fr_70px] gap-2 px-4 py-2 text-sm items-center">
              <span className="text-anthracite-400 text-xs font-mono">{new Date(e.timestamp).toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", day: "2-digit", month: "2-digit" })}</span>
              <span className="text-anthracite-200 truncate">{e.username || "--"}</span>
              <span className="text-anthracite-100 font-mono text-xs truncate">{e.action}</span>
              <span className="text-anthracite-300 truncate" title={e.error_message || ""}>
                {e.resource || "--"}
                {e.error_message && <span className="text-status-error"> — {e.error_message}</span>}
              </span>
              <span>
                {e.result === "succes"
                  ? <CheckCircle2 size={14} className="text-status-running" />
                  : <XCircle size={14} className="text-status-error" />}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
