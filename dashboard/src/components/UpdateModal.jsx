import { useEffect, useState } from "react";
import { RefreshCw, ShieldAlert, CheckCircle2, XCircle } from "lucide-react";
import ProgressBar from "./ProgressBar";
import { fetchUpdateCheck, applyUpdate, fetchTaskDetail } from "../api/client";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

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
export default function UpdateModal({ open, onClose, triggerRef }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | updating | restarting | ok | failed
  const [task, setTask] = useState(null);

  useEffect(() => {
    // Guarded on `open`: the component now stays mounted while closed (see the
    // note in VMWizard.jsx), so without this it would call the backend on
    // every page load instead of only when the modal is actually opened.
    if (!open) return;
    fetchUpdateCheck().then(setInfo).catch((e) => setError(e.message));
  }, [open]);

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
      // The service restarts ~2 s after the task ends: we wait for /health to answer. With APT
      // the target version is known, so success also requires the RUNNING version to be that
      // one: when the server watchdog rolls a broken release back, /health answers again but
      // on the previous version, which must not be reported as a successful update.
      const target = info?.branche === "apt" ? info.commit_distant : null;
      const deadline = Date.now() + 120000;
      let runningVersion = null;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const res = await fetch("/health");
          if (res.ok) {
            runningVersion = (await res.json()).hyperlite_version ?? null;
            if (!target || runningVersion === target) { setPhase("ok"); return; }
          }
        } catch { /* service restarting, expected */ }
      }
      setPhase("failed");
      setError(runningVersion
        ? `The new version did not start: the service is running ${runningVersion} again (the update was rolled back). Check the server logs before retrying.`
        : "The service no longer responds after 120 s. The server watchdog may have had to restore the previous version: check manually.");
    } catch (e) {
      setPhase("failed");
      setError(e.message);
    }
  }

  const currentStepIdx = task ? STEP_ORDER.findIndex(([pct]) => pct >= (task.progres ?? 0)) : -1;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent
        className="w-[520px] max-w-[90vw]"
        // See the note in VMWizard.jsx: explicit focus restore to the trigger
        // rather than relying on Radix's implicit capture (this modal is
        // opened from a DropdownMenuItem, not a direct DialogTrigger click).
        onCloseAutoFocus={(e) => {
          if (triggerRef?.current) {
            e.preventDefault();
            triggerRef.current.focus();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>Hyperlite update</DialogTitle>
        </DialogHeader>

        {error && <p className="text-sm text-status-error">{error}</p>}

        {phase === "idle" && info && (
          <>
            {!info.verifiable && <p className="text-sm text-foreground/80">{info.erreur}</p>}
            {info.verifiable && (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">Local version</span><span className="font-mono text-foreground/90">{shortVersion(info.commit_local)}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Remote version</span><span className="font-mono text-foreground/90">{shortVersion(info.commit_distant) ?? "--"}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Status</span><span className={info.a_jour ? "text-status-running" : "text-status-warning"}>{info.a_jour ? "Up to date" : "New version available"}</span></div>

                {info.changelog?.length > 0 && (
                  <div className="mt-2 rounded-md bg-muted/60 p-2 max-h-32 overflow-y-auto font-mono text-xs text-foreground/80">
                    {info.changelog.map((l, i) => <div key={i}>{l}</div>)}
                  </div>
                )}

                {!info.arbre_propre && (
                  <div className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 mt-2">
                    <ShieldAlert size={14} className="text-status-warning shrink-0 mt-0.5" />
                    <p className="text-xs text-foreground/90">
                      Working tree is not clean (uncommitted changes): the update is blocked to avoid a conflict. Commit or discard the local changes first.
                    </p>
                  </div>
                )}

                <p className="text-xs text-muted-foreground mt-2">
                  It only touches the Hyperlite API and interface: VMs that are already running are neither stopped nor restarted. A full backup is taken before any change, with automatic restoration on failure.
                </p>
              </div>
            )}
            <DialogFooter>
              <Button variant="secondary" onClick={onClose}>Close</Button>
              <Button
                disabled={!info.verifiable || info.a_jour || !info.arbre_propre}
                onClick={handleApply}
              >
                <RefreshCw /> Update
              </Button>
            </DialogFooter>
          </>
        )}

        {(phase === "updating" || phase === "restarting") && (
          <div className="space-y-3">
            <ProgressBar value={task?.progres ?? 5} statut="en_cours" />
            <div className="text-sm text-foreground/90">
              {phase === "restarting" ? "Restarting the service, verification in progress..." : (STEP_ORDER[currentStepIdx]?.[1] ?? "Preparing...")}
            </div>
            <p className="text-xs text-muted-foreground">Do not close this window.</p>
          </div>
        )}

        {phase === "ok" && (
          <div className="flex flex-col items-center gap-2 py-4">
            <CheckCircle2 size={32} className="text-status-running" />
            <p className="text-sm text-foreground">Update applied, the service is responding.</p>
            <Button className="mt-2" onClick={() => window.location.reload()}>Reload the page</Button>
          </div>
        )}

        {phase === "failed" && (
          <div className="flex flex-col items-center gap-2 py-4">
            <XCircle size={32} className="text-status-error" />
            <p className="text-sm text-foreground text-center">Update failed.</p>
            <Button variant="secondary" className="mt-2" onClick={onClose}>Close</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
