import { useCallback, useEffect, useState } from "react";
import { fetchHostProfile, setHostProfile, setHostAllocation } from "../api/client";
import { useAuthStore, selectIsAdmin } from "../store/useAuthStore";
import { useInfraStore } from "../store/useInfraStore";

const SOURCE = { detecte: "détecté", choisi: "choisi par un admin", configuration: "forcé par HYPERLITE_PROFILE" };

// Profils de deploiement (mandat portabilite, chantier 5) : GET/PUT
// /host/profile. Ce sont des reglages par defaut, pas des produits differents.
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
      pushToast({ kind: "success", title: "Profil mis à jour", message: profil === "auto" ? "Détection automatique" : profil });
    } catch (e) {
      pushToast({ kind: "error", title: "Changement de profil impossible", message: e.message });
    } finally { setBusy(false); }
  }, [pushToast]);

  const chooseAllocation = useCallback(async (politique) => {
    setBusy(true);
    try {
      setData(await setHostAllocation(politique));
      pushToast({ kind: "success", title: "Politique d'allocation mise à jour", message: politique });
    } catch (e) {
      pushToast({ kind: "error", title: "Changement impossible", message: e.message });
    } finally { setBusy(false); }
  }, [pushToast]);

  if (!data) return null;
  const alloc = data.allocation;
  const allocForced = alloc.source === "configuration";
  const forced = data.source === "configuration";
  const options = [["auto", `Auto (recommandé : ${data.profils[data.recommande].libelle})`], ...Object.entries(data.profils).map(([k, p]) => [k, p.libelle])];

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-anthracite-600 flex items-center justify-between gap-4">
        <div className="text-sm font-semibold text-anthracite-100">
          Profil de déploiement : {data.profils[data.actif].libelle}
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
          RAM allouable à une VM : {Math.round(data.reglages.memory_host_share * 100)}% · disque : {Math.round(data.reglages.disk_free_share * 100)}% du libre · métriques toutes les {data.reglages.metrics_interval_s}s ·
          VM par défaut : {data.vm_defaults_effectifs.vcpu} vCPU / {data.vm_defaults_effectifs.memory_mb} Mo / {data.vm_defaults_effectifs.disk_gb} Go
        </p>
        {forced && <p className="text-status-warning">Forcé par la variable d'environnement HYPERLITE_PROFILE : modifiable uniquement côté serveur.</p>}
      </div>
      <div className="border-t border-anthracite-600 px-4 py-3 space-y-2">
        <div className="flex items-center justify-between gap-4">
          <div className="text-sm font-semibold text-anthracite-100">Attribution des ressources aux VM : {alloc.politiques[alloc.actif].libelle}</div>
          <select className="input w-64" value={alloc.actif} disabled={!isAdmin || busy || allocForced} onChange={(e) => chooseAllocation(e.target.value)}>
            {Object.entries(alloc.politiques).map(([k, p]) => <option key={k} value={k}>{p.libelle}</option>)}
          </select>
        </div>
        <p className="text-sm text-anthracite-300">{alloc.politiques[alloc.actif].description}</p>
        {allocForced && <p className="text-sm text-status-warning">Forcée par la variable d'environnement HYPERLITE_ALLOCATION : modifiable uniquement côté serveur.</p>}
      </div>
    </div>
  );
}
