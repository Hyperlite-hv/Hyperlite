import { useCallback, useEffect, useState } from "react";
import { fetchHostProfile, setHostProfile, setHostAllocation } from "../api/client";
import { useAuthStore, selectIsAdmin } from "../store/useAuthStore";
import { useInfraStore } from "../store/useInfraStore";

const SOURCE = { detecte: "detected", choisi: "chosen by an admin", configuration: "forced by HYPERLITE_PROFILE" };

// Deployment profiles: GET/PUT /host/profile. These are default settings, not
// different products.
export default function DeploymentProfileCard() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { fetchHostProfile().then(setData).catch(() => {}); }, []);

  const choose = useCallback(async (profil) => {
    setBusy(true);
    try {
      setData(await setHostProfile(profil));
      pushToast({ kind: "success", title: "Profile updated", message: profil === "auto" ? "Automatic detection" : profil });
    } catch (e) {
      pushToast({ kind: "error", title: "Profile change impossible", message: e.message });
    } finally { setBusy(false); }
  }, [pushToast]);

  const chooseAllocation = useCallback(async (politique) => {
    setBusy(true);
    try {
      setData(await setHostAllocation(politique));
      pushToast({ kind: "success", title: "Allocation policy updated", message: politique });
    } catch (e) {
      pushToast({ kind: "error", title: "Change impossible", message: e.message });
    } finally { setBusy(false); }
  }, [pushToast]);

  if (!data) return null;
  const alloc = data.allocation;
  const allocForced = alloc.source === "configuration";
  const forced = data.source === "configuration";
  const options = [["auto", `Auto (recommended: ${data.profils[data.recommande].libelle})`], ...Object.entries(data.profils).map(([k, p]) => [k, p.libelle])];

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-anthracite-600 flex items-center justify-between gap-4">
        <div className="text-sm font-semibold text-anthracite-100">
          Deployment profile: {data.profils[data.actif].libelle}
          <span className="ml-2 text-xs font-normal text-anthracite-300">({SOURCE[data.source]})</span>
        </div>
        <select
          className="input w-64"
          value={data.choix in data.profils ? data.choix : "auto"}
          disabled={!isAdmin || busy || forced}
          onChange={(e) => choose(e.target.value)}
        >
          {options.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      </div>
      <div className="px-4 py-3 text-sm text-anthracite-300 space-y-1">
        <p>{data.reglages.description}</p>
        <p className="font-mono text-xs text-anthracite-100">
          RAM allocatable to a VM: {Math.round(data.reglages.memory_host_share * 100)}% · disk: {Math.round(data.reglages.disk_free_share * 100)}% of free space · metrics every {data.reglages.metrics_interval_s}s · default VM: {data.vm_defaults_effectifs.vcpu} vCPU / {data.vm_defaults_effectifs.memory_mb} MB / {data.vm_defaults_effectifs.disk_gb} GB
        </p>
        {forced && <p className="text-status-warning">Forced by the HYPERLITE_PROFILE environment variable: can only be changed on the server side.</p>}
      </div>
      <div className="border-t border-anthracite-600 px-4 py-3 space-y-2">
        <div className="flex items-center justify-between gap-4">
          <div className="text-sm font-semibold text-anthracite-100">Resource allocation to VMs: {alloc.politiques[alloc.actif].libelle}</div>
          <select className="input w-64" value={alloc.actif} disabled={!isAdmin || busy || allocForced} onChange={(e) => chooseAllocation(e.target.value)}>
            {Object.entries(alloc.politiques).map(([k, p]) => <option key={k} value={k}>{p.libelle}</option>)}
          </select>
        </div>
        <p className="text-sm text-anthracite-300">{alloc.politiques[alloc.actif].description}</p>
        {allocForced && <p className="text-sm text-status-warning">Forced by the HYPERLITE_ALLOCATION environment variable: can only be changed on the server side.</p>}
      </div>
    </div>
  );
}
