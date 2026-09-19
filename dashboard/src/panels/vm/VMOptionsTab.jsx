import { useCallback, useEffect, useState } from "react";
import { Save, Gauge } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { fetchVMLimits, setVMLimits } from "../../api/client";
import { useHostLimits } from "../../hooks/useHostLimits";

// Real: PATCH /vms/{name} (added to allow what was missing the most: changing the
// vCPU/RAM of an existing VM without having to recreate it). Requires the VM to
// be stopped, as with an offline resize on Proxmox.
export default function VMOptionsTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const updateVMResources = useInfraStore((s) => s.updateVMResources);
  const hostLimits = useHostLimits();
  const [vcpu, setVcpu] = useState(vm?.vcpu ?? 1);
  const [memoryMb, setMemoryMb] = useState(vm?.memoire_mo ?? 512);
  const [busy, setBusy] = useState(false);

  useEffect(() => { setVcpu(vm?.vcpu ?? 1); setMemoryMb(vm?.memoire_mo ?? 512); }, [vm?.nom, vm?.vcpu, vm?.memoire_mo]);

  if (!vm) return null;
  const dirty = vcpu !== vm.vcpu || memoryMb !== vm.memoire_mo;

  async function handleSave() {
    setBusy(true);
    try {
      await updateVMResources(vm.nom, { vcpu, memory_mb: memoryMb });
    } catch {
      // error already pushed as a toast by the store
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="card divide-y divide-anthracite-600">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="text-sm text-anthracite-100">Hostname</div>
          <div className="text-sm text-anthracite-300 font-mono">{vm.nom}</div>
        </div>
        <div className="flex items-center justify-between px-4 py-3">
          <div>
            <div className="text-sm text-anthracite-100">vCPU</div>
            <div className="text-xs text-anthracite-400">{hostLimits ? `${hostLimits.vcpu.min} to ${hostLimits.vcpu.max}` : "Limit set by the host"} -- stopped VM required</div>
          </div>
          <input aria-label="vCPU count"
            type="number" min={hostLimits?.vcpu.min ?? 1} max={hostLimits?.vcpu.max} className="input w-24" disabled={!isAdmin || vm.etat === "actif"}
            value={vcpu} onChange={(e) => setVcpu(Number(e.target.value))}
          />
        </div>
        <div className="flex items-center justify-between px-4 py-3">
          <div>
            <div className="text-sm text-anthracite-100">Memory (MB)</div>
            <div className="text-xs text-anthracite-400">{hostLimits ? `${hostLimits.memoire_mo.min} to ${hostLimits.memoire_mo.max}` : "Limit set by the host"} -- stopped VM required</div>
          </div>
          <input aria-label="Memory in MB"
            type="number" min={hostLimits?.memoire_mo.min ?? 256} max={hostLimits?.memoire_mo.max} step={128} className="input w-24" disabled={!isAdmin || vm.etat === "actif"}
            value={memoryMb} onChange={(e) => setMemoryMb(Number(e.target.value))}
          />
        </div>
        {isAdmin && (
          <div className="flex items-center justify-end px-4 py-3">
            {vm.etat === "actif" && <span className="mr-auto text-xs text-anthracite-400">Stop the VM to change its resources.</span>}
            <button className="btn-primary" disabled={!dirty || busy || vm.etat === "actif"} onClick={handleSave}>
              <Save size={13} /> Save
            </button>
          </div>
        )}
      </div>

      <VMLimitsCard vm={vm} isAdmin={isAdmin} pushToast={pushToast} />
    </div>
  );
}

// Real: GET/PUT /vms/{name}/limits (cgroups through libvirt, see
// app/routers/vms.py). Applies live AND when stopped (no need to stop the VM,
// unlike the vCPU/RAM resize above, which touches the frozen domain config).
function VMLimitsCard({ vm, isAdmin, pushToast }) {
  const [limits, setLimits] = useState(null);
  const [shares, setShares] = useState(1024);
  const [cpuLimitPct, setCpuLimitPct] = useState("");
  const [memHardLimitMb, setMemHardLimitMb] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    fetchVMLimits(vm.nom).then((l) => {
      setLimits(l);
      setShares(l.cpu_shares);
      setCpuLimitPct(l.cpu_limit_pct ?? "");
      setMemHardLimitMb(l.mem_hard_limit_mb ?? "");
    }).catch((e) => pushToast({ kind: "error", title: "Limits error", message: e.message }));
  }, [vm.nom, pushToast]);

  useEffect(() => { load(); }, [load]);

  if (!limits) return null;

  const dirty = shares !== limits.cpu_shares
    || (cpuLimitPct || null) != (limits.cpu_limit_pct ?? null)
    || (memHardLimitMb || null) != (limits.mem_hard_limit_mb ?? null);

  async function handleSave() {
    setBusy(true);
    try {
      const updated = await setVMLimits(vm.nom, {
        cpu_shares: Number(shares),
        cpu_limit_pct: cpuLimitPct === "" ? null : Number(cpuLimitPct),
        mem_hard_limit_mb: memHardLimitMb === "" ? null : Number(memHardLimitMb),
      });
      setLimits(updated);
      pushToast({ kind: "success", title: "Limits applied", message: vm.nom });
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card divide-y divide-anthracite-600">
      <div className="flex items-center gap-2 px-4 py-3">
        <Gauge size={14} className="text-anthracite-400" />
        <div className="text-sm font-medium text-anthracite-100">Limits and priority (cgroups)</div>
      </div>

      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">CPU priority (shares)</div>
          <div className="text-xs text-anthracite-400">Relative to the other VMs under real host contention. 1024 = normal.</div>
        </div>
        <input aria-label="CPU shares" type="number" min={2} max={262144} className="input w-28" disabled={!isAdmin}
          value={shares} onChange={(e) => setShares(e.target.value)} />
      </div>

      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">Max CPU limit (% per vCPU)</div>
          <div className="text-xs text-anthracite-400">Hard cap, even if the host is idle. Empty = unlimited.</div>
        </div>
        <input aria-label="Max CPU limit percent per vCPU" type="number" min={1} max={100} placeholder="unlimited" className="input w-28" disabled={!isAdmin}
          value={cpuLimitPct} onChange={(e) => setCpuLimitPct(e.target.value)} />
      </div>

      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">RAM limit (MB)</div>
          <div className="text-xs text-anthracite-400">Hard cgroup cap, distinct from the RAM allocated above. Empty = unlimited.</div>
        </div>
        <input aria-label="RAM limit in MB" type="number" min={64} placeholder="unlimited" className="input w-28" disabled={!isAdmin}
          value={memHardLimitMb} onChange={(e) => setMemHardLimitMb(e.target.value)} />
      </div>

      {isAdmin && (
        <div className="flex items-center justify-end px-4 py-3">
          <span className="mr-auto text-xs text-anthracite-400">See real usage in the Summary tab.</span>
          <button className="btn-primary" disabled={!dirty || busy} onClick={handleSave}>
            <Save size={13} /> Apply
          </button>
        </div>
      )}
    </div>
  );
}
