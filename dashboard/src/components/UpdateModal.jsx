import { useEffect, useState } from "react";
import { X, RefreshCw, ShieldAlert, CheckCircle2, XCircle } from "lucide-react";
import ProgressBar from "./ProgressBar";
import { fetchUpdateCheck, applyUpdate, fetchTaskDetail } from "../api/client";

const STEP_ORDER = [
  [5, "Backing up the current state"],
  [20, "Fetching the latest version"],
  [30, "Applying the new version"],
  [45, "Python dependencies"],
  [55, "Frontend dependencies"],
  [70, "Rebuilding the interface"],
  [85, "Schema check"],
  [90, "Restarting the service"],
];

// Real: GET /update/check + POST /update/apply (see app/routers/update.py). The
// final restart is verified on the BROWSER side (polling /health): the backend
// driving the update dies with the restart and cannot verify its own replacement
// (see scripts/update_watchdog.sh for the real server-side safety net).

// commit_local/commit_distant carries either a Git hash (git mode) or a .deb
// package version number (an appliance moved to the APT repository, apt mode): the
// same response shape on the backend for both, only the display differs. A Git
// hash never contains a dot while a .deb version number always does, a heuristic
// that is enough to truncate only real hashes.
function shortVersion(v) {
  if (!v) return v;
  return v.includes(".") ? v : v.slice(0, 8);
}
export default function UpdateModal({ onClose }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | updating | restarting | ok | failed
  const [task, setTask] = useState(null);

  useEffect(() => {
    fetchUpdateCheck().then(setInfo).catch((e) => setError(e.message));
  }, []);

  async function handleApply() {
    setPhase("updating");
    setError(null);
    try {
      const { task_id } = await applyUpdate();
      for (;;) {
        const t = await fetchTaskDetail(task_id);
        setTask(t);
        if (t.statut === "echec") { setPhase("failed"); setError(t.erreur); return; }
        if (t.statut === "termine") break;
        await new Promise((r) => setTimeout(r, 1000));
      }
      setPhase("restarting");
      // The service restarts ~2 s after the task ends: we wait for an answer from
      // /health, with a generous delay (frontend build + restart).
      const deadline = Date.now() + 60000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const res = await fetch("/health");
          if (res.ok) { setPhase("ok"); return; }
        } catch { /* service restarting, expected */ }
      }
      setPhase("failed");
      setError("The service no longer responds after 60 s. The server watchdog may have had to restore the previous version: check manually.");
    } catch (e) {
      setPhase("failed");
      setError(e.message);
    }
  }

  const currentStepIdx = task ? STEP_ORDER.findIndex(([pct]) => pct >= (task.progres ?? 0)) : -1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-[520px] max-w-[90vw] p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-anthracite-100">Hyperlite update</h3>
          <button onClick={onClose} className="text-anthracite-400 hover:text-anthracite-100"><X size={18} /></button>
        </div>

        {error && <p className="text-sm text-status-error">{error}</p>}

        {phase === "idle" && info && (
          <>
            {!info.verifiable && <p className="text-sm text-anthracite-300">{info.erreur}</p>}
            {info.verifiable && (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-anthracite-400">Local version</span><span className="font-mono text-anthracite-200">{shortVersion(info.commit_local)}</span></div>
                <div className="flex justify-between"><span className="text-anthracite-400">Remote version</span><span className="font-mono text-anthracite-200">{shortVersion(info.commit_distant) ?? "--"}</span></div>
                <div className="flex justify-between"><span className="text-anthracite-400">Status</span><span className={info.a_jour ? "text-status-running" : "text-status-warning"}>{info.a_jour ? "Up to date" : "New version available"}</span></div>

                {info.changelog?.length > 0 && (
                  <div className="mt-2 rounded-md bg-anthracite-700/60 p-2 max-h-32 overflow-y-auto font-mono text-xs text-anthracite-300">
                    {info.changelog.map((l, i) => <div key={i}>{l}</div>)}
                  </div>
                )}

                {!info.arbre_propre && (
                  <div className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 mt-2">
                    <ShieldAlert size={14} className="text-status-warning shrink-0 mt-0.5" />
                    <p className="text-xs text-anthracite-200">
                      Working tree is not clean (uncommitted changes): the update is blocked to avoid a conflict. Commit or discard the local changes first.
                    </p>
                  </div>
                )}

                <p className="text-xs text-anthracite-500 mt-2">
                  It only touches the Hyperlite API and interface: VMs that are already running are neither stopped nor restarted. A full backup is taken before any change, with automatic restoration on failure.
                </p>
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button className="btn-secondary" onClick={onClose}>Close</button>
              <button
                className="btn-primary"
                disabled={!info.verifiable || info.a_jour || !info.arbre_propre}
                onClick={handleApply}
              >
                <RefreshCw size={14} /> Update
              </button>
            </div>
          </>
        )}

        {(phase === "updating" || phase === "restarting") && (
          <div className="space-y-3">
            <ProgressBar value={task?.progres ?? 5} statut="en_cours" />
            <div className="text-sm text-anthracite-200">
              {phase === "restarting" ? "Restarting the service, verification in progress..." : (STEP_ORDER[currentStepIdx]?.[1] ?? "Preparing...")}
            </div>
            <p className="text-xs text-anthracite-500">Do not close this window.</p>
          </div>
        )}

        {phase === "ok" && (
          <div className="flex flex-col items-center gap-2 py-4">
            <CheckCircle2 size={32} className="text-status-running" />
            <p className="text-sm text-anthracite-100">Update applied, the service is responding.</p>
            <button className="btn-primary mt-2" onClick={() => window.location.reload()}>Reload the page</button>
          </div>
        )}

        {phase === "failed" && (
          <div className="flex flex-col items-center gap-2 py-4">
            <XCircle size={32} className="text-status-error" />
            <p className="text-sm text-anthracite-100 text-center">Update failed.</p>
            <button className="btn-secondary mt-2" onClick={onClose}>Close</button>
          </div>
        )}
      </div>
    </div>
  );
}
