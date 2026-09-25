import LoadingState from "../../components/LoadingState";
import { useCallback, useEffect, useState, useRef } from "react";
import { Camera, RotateCcw, Trash2, AlertTriangle } from "lucide-react";
import ConfirmDialog from "../../components/ConfirmDialog";
import ProgressBar from "../../components/ProgressBar";
import { fetchSnapshots, createSnapshot, restoreSnapshot, deleteSnapshot, fetchTaskDetail } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

// Real: GET/POST /vms/{name}/snapshots + POST .../restore?confirm=true + DELETE
// .../{snapshot_name}.
// - create/restore answer 202 + a task_id (the operation runs in the background on
//   the backend, potentially for several seconds while the VM memory is
//   (de)serialized). We follow the real task through GET /tasks/{id} (see
//   api/client.js) instead of blocking on the fetch or inventing a percentage that
//   libvirt does not expose.
// - `parent` is a real field returned by the backend (getParent()), not invented on
//   the frontend: it is used to indent the tree of nested snapshots.
function formatElapsed(startedAt) {
  const s = Math.round((Date.now() - startedAt) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function VMSnapshotsTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [snapshots, setSnapshots] = useState(null);
  const [pending, setPending] = useState(null); // { action: "restore"|"delete", snap }
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState(null); // { label, startedAt } while a create/restore is in progress
  const [, forceTick] = useState(0);

  const reload = useCallback(async () => {
    try { setSnapshots(await fetchSnapshots(vm.nom)); }
    catch (e) { pushToast({ kind: "error", title: "Snapshots error", message: e.message }); }
  }, [vm?.nom, pushToast]);

  useEffect(() => { if (vm?.nom) reload(); }, [vm?.nom, reload]);

  // Only refreshes the "Xs elapsed" counter while a job is running.
  useEffect(() => {
    if (!job) return;
    const id = setInterval(() => forceTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [job]);

  // Bounded wait (5 minutes) that stops when the tab is left: a stuck task can no longer poll forever.
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const waitTask = useCallback(async (taskId) => {
    for (let i = 0; i < 300; i++) {
      if (!alive.current) return { statut: "abandonne", erreur: null };
      const t = await fetchTaskDetail(taskId);
      if (t.statut !== "en_cours") return t;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return { statut: "echec", erreur: "Still running after 5 minutes: check the Activity page for the final result." };
  }, []);

  if (!vm) return null;
  if (snapshots == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  // VM on a ZFS pool: read directly from vm.stockage_zfs (GET /vms,
  // app/routers/vms.py::_domain_summary). The first version deduced it from the list
  // of EXISTING snapshots (etat_vm=='disque_seul'), which is wrong for the VERY FIRST
  // snapshot of a VM (an empty list while it is being created), so the qcow2 wording
  // ("memory included automatically") was wrongly shown during that very first
  // snapshot. The qcow2 wording is wrong for these VMs in the absolute anyway: a ZFS
  // snapshot never grows a qcow2 file (there is none) and NEVER includes memory, even
  // if the VM is running when the snapshot is taken.
  const isZfsBacked = Boolean(vm.stockage_zfs);

  async function handleCreate() {
    setBusy(true);
    const name = `snap-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}`;
    try {
      const { task_id } = await createSnapshot(vm.nom, name, "Created from the dashboard");
      setJob({ label: `Creating "${name}"`, startedAt: Date.now() });
      const t = await waitTask(task_id);
      if (t.statut === "termine") {
        pushToast({ kind: "success", title: "Snapshot created", message: name });
      } else {
        pushToast({ kind: "error", title: "Creation failed", message: t.erreur || "Unknown error" });
      }
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); setJob(null); }
  }

  async function confirmAction() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.action === "delete") {
        await deleteSnapshot(vm.nom, pending.snap.nom);
        pushToast({ kind: "success", title: "Snapshot deleted", message: pending.snap.nom });
        await reload();
      } else {
        const { task_id } = await restoreSnapshot(vm.nom, pending.snap.nom);
        setJob({ label: `Restoring to "${pending.snap.nom}"`, startedAt: Date.now() });
        const t = await waitTask(task_id);
        if (t.statut === "termine") {
          pushToast({ kind: "success", title: "Snapshot restored", message: pending.snap.nom });
        } else {
          pushToast({ kind: "error", title: "Restore failed", message: t.erreur || "Unknown error" });
        }
        await reload();
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally {
      setBusy(false);
      setJob(null);
      setPending(null);
    }
  }

  // Indentation depth of a snapshot in the tree (through `parent`, up to the root):
  // a simple display, not a real graph view.
  const depthOf = (snap, seen = new Set()) => {
    if (!snap.parent || seen.has(snap.nom)) return 0;
    seen.add(snap.nom);
    const parent = snapshots.find((s) => s.nom === snap.parent);
    return parent ? 1 + depthOf(parent, seen) : 0;
  };

  return (
    <div className="space-y-3">
      {snapshots.length >= 3 && (
        <div className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2">
          <AlertTriangle size={15} className="text-status-warning shrink-0 mt-0.5" />
          <p className="text-xs text-foreground/80">
            {snapshots.length} active snapshots on this VM.{" "}
            {isZfsBacked
              ? "Every ZFS snapshot kept takes space on the pool: delete the ones that are no longer useful as soon as possible."
              : "Every snapshot kept slows the disk down and grows the qcow2 file: delete the ones that are no longer useful as soon as possible."}
          </p>
        </div>
      )}

      {isAdmin && (
        <Button disabled={busy} onClick={handleCreate}>
          <Camera /> Create a snapshot
        </Button>
      )}

      {job && (
        <Card className="p-3 space-y-1.5 animate-in fade-in-0 duration-150">
          <div className="flex items-center justify-between text-sm">
            <span className="text-foreground">{job.label}</span>
            <span className="text-xs text-muted-foreground">{formatElapsed(job.startedAt)} elapsed</span>
          </div>
          <ProgressBar indeterminate statut="en_cours" />
          <p className="text-[11px] text-muted-foreground">
            {isZfsBacked
              ? "Native ZFS snapshot (disk only, nearly instantaneous)."
              : "May take several seconds if the VM is running (memory is included automatically)."}
          </p>
        </Card>
      )}

      <Card className="p-0 divide-y divide-border">
        {snapshots.length === 0 && <div className="px-4 py-3 text-sm text-muted-foreground">No snapshots.</div>}
        {snapshots.map((s) => (
          <div key={s.nom} className="flex items-center gap-3 px-4 py-2.5 transition-colors duration-150 hover:bg-muted/40" style={{ paddingLeft: `${16 + depthOf(s) * 20}px` }}>
            <Camera size={14} className="text-muted-foreground shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-sm text-foreground">
                {s.nom} {s.actuel && <Badge variant="secondary" className="ml-1 bg-accent-blue/20 text-accent-blue">current</Badge>}
              </div>
              <div className="text-xs text-muted-foreground truncate">
                {s.description || "--"} {s.date_creation ? `-- ${s.date_creation}` : ""}
                {s.etat_vm === "disque_seul"
                  ? " -- ZFS, disk only (never memory)"
                  : s.etat_vm && ` -- VM ${s.etat_vm === "running" ? "running (memory included)" : "stopped (disk only)"}`}
              </div>
            </div>
            {isAdmin && (
              <>
                <Button aria-label={`Restore snapshot ${s.nom}`} variant="secondary" size="sm" disabled={busy} onClick={() => setPending({ action: "restore", snap: s })}><RotateCcw /> Restore</Button>
                <Button aria-label={`Delete snapshot ${s.nom}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" disabled={busy} onClick={() => setPending({ action: "delete", snap: s })}><Trash2 size={13} /></Button>
              </>
            )}
          </div>
        ))}
      </Card>

      <ConfirmDialog
        open={!!pending}
        title={pending?.action === "delete" ? `Delete '${pending.snap.nom}'?` : `Restore '${pending?.snap.nom}'?`}
        message={pending?.action === "delete" ? "This action is irreversible." : "The current state of the VM will be replaced by that of the snapshot."}
        confirmLabel={pending?.action === "delete" ? "Delete" : "Restore"}
        danger={pending?.action === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
