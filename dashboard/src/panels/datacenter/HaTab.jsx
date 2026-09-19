import LoadingState from "../../components/LoadingState";
import { confirmAction } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, ShieldOff, LifeBuoy, RefreshCw } from "lucide-react";
import StatusBadge from "../../components/StatusBadge";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchHaProtected, disableHa, recoverHa } from "../../api/client";

function formatDate(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// Lists the protected VMs (GET /ha) with the REAL status of their node, and lets
// an admin trigger a manual recovery, never an automatic one (see app/core/ha.py
// for why: without fencing, automatically restarting a protected VM while the
// original still runs on the same shared disk would corrupt the data).
export default function HaTab() {
  const nodes = useInfraStore((s) => s.nodes);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [recoverTarget, setRecoverTarget] = useState({}); // vm_name -> chosen node
  const [busy, setBusy] = useState(null);

  const reload = useCallback(async () => {
    try { setRows(await fetchHaProtected()); }
    catch (e) { pushToast({ kind: "error", title: "HA error", message: e.message }); }
  }, [pushToast]);

  useEffect(() => {
    reload();
    const id = setInterval(reload, 15000);
    return () => clearInterval(id);
  }, [reload]);

  async function handleDisable(vmName) {
    if (!(await confirmAction({ title: "Please confirm", message: `Disable HA protection for '${vmName}'?`, confirmLabel: "Confirm" }))) return;
    try {
      await disableHa(vmName);
      pushToast({ kind: "success", title: "Protection disabled", message: vmName });
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  async function handleRecover(vmName) {
    const target = recoverTarget[vmName];
    if (!target) return;
    setBusy(vmName);
    try {
      await recoverHa(vmName, target);
      pushToast({ kind: "success", title: "VM recovered", message: `${vmName} on ${target}` });
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Recovery failed", message: e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5">
      <div className="card p-4">
        <p className="text-sm text-anthracite-300">
          The protected VMs below must have all their disks on a <b>shared</b> storage pool (NFS, Storage tab). If the node of a protected VM goes down, an alert appears here. Recovery to another node is always <b>triggered manually</b> by an admin (never automatic, to avoid any risk of corruption if the node is in fact only temporarily unreachable).
        </p>
      </div>

      <div className="card divide-y divide-anthracite-600">
        <div className="grid grid-cols-5 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
          <span>VM</span><span>Current node</span><span>Node status</span><span>Last sync</span><span />
        </div>
        {rows == null && <div className="px-4 py-3 text-sm text-anthracite-400"><LoadingState /></div>}
        {rows && rows.length === 0 && (
          <div className="px-4 py-6 text-sm text-anthracite-400 text-center">
            No protected VMs. Enable HA protection from the Summary tab of a running VM (a disk on a shared pool is required).
          </div>
        )}
        {rows && rows.map((r) => {
          const down = r.statut_noeud === "hors_ligne";
          const targets = nodes.filter((n) => n.id !== r.node && n.etat === "online");
          return (
            <div key={r.vm_name} className="grid grid-cols-5 gap-2 px-4 py-3 text-sm items-center">
              <span className="text-anthracite-100 font-medium flex items-center gap-2">
                {down ? <ShieldOff size={14} className="text-status-error shrink-0" /> : <ShieldCheck size={14} className="text-status-running shrink-0" />}
                {r.vm_name}
              </span>
              <span className="text-anthracite-300">{r.node}</span>
              <span><StatusBadge etat={down ? "erreur" : "actif"} /></span>
              <span className="text-anthracite-400 text-xs font-mono">{formatDate(r.last_synced_at)}</span>
              <div className="flex items-center justify-end gap-2">
                {down && (
                  <>
                    <select aria-label="Recovery node"
                      className="input w-auto text-xs py-1"
                      value={recoverTarget[r.vm_name] || ""}
                      onChange={(e) => setRecoverTarget({ ...recoverTarget, [r.vm_name]: e.target.value })}
                    >
                      <option value="">Recover on…</option>
                      {targets.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
                    </select>
                    <button
                      className="btn-primary py-1!"
                      disabled={!recoverTarget[r.vm_name] || busy === r.vm_name}
                      onClick={() => handleRecover(r.vm_name)}
                    >
                      <LifeBuoy size={13} /> {busy === r.vm_name ? "..." : "Recover"}
                    </button>
                  </>
                )}
                <button className="btn-secondary py-1!" onClick={() => handleDisable(r.vm_name)}>
                  <ShieldOff size={13} /> Disable
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <button className="text-xs text-accent-blue hover:underline flex items-center gap-1.5" onClick={reload}>
        <RefreshCw size={12} /> Refresh
      </button>
    </div>
  );
}
