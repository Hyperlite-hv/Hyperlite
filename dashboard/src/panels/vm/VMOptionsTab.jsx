import { useCallback, useEffect, useState } from "react";
import { Save, Gauge } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { fetchVMLimits, setVMLimits } from "../../api/client";
import { useHostLimits } from "../../hooks/useHostLimits";

// Reel : PATCH /vms/{name} (ajoute pour permettre ce qui manquait le plus --
// changer vCPU/RAM d'une VM existante sans devoir la recreer). Necessite la
// VM arretee, comme cote Proxmox pour un redimensionnement hors-ligne.
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
    } catch (e) {
      // erreur deja poussee en toast par le store
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="card divide-y divide-anthracite-600">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="text-sm text-anthracite-100">Nom d'hôte</div>
          <div className="text-sm text-anthracite-300 font-mono">{vm.nom}</div>
        </div>
        <div className="flex items-center justify-between px-4 py-3">
          <div>
            <div className="text-sm text-anthracite-100">vCPU</div>
            <div className="text-xs text-anthracite-400">{hostLimits ? `${hostLimits.vcpu.min} à ${hostLimits.vcpu.max}` : "Limite fixée par l'hôte"} -- VM arrêtée requise</div>
          </div>
          <input
            type="number" min={hostLimits?.vcpu.min ?? 1} max={hostLimits?.vcpu.max} className="input w-24" disabled={!isAdmin || vm.etat === "actif"}
            value={vcpu} onChange={(e) => setVcpu(Number(e.target.value))}
          />
        </div>
        <div className="flex items-center justify-between px-4 py-3">
          <div>
            <div className="text-sm text-anthracite-100">Mémoire (Mo)</div>
            <div className="text-xs text-anthracite-400">{hostLimits ? `${hostLimits.memoire_mo.min} à ${hostLimits.memoire_mo.max}` : "Limite fixée par l'hôte"} -- VM arrêtée requise</div>
          </div>
          <input
            type="number" min={hostLimits?.memoire_mo.min ?? 256} max={hostLimits?.memoire_mo.max} step={128} className="input w-24" disabled={!isAdmin || vm.etat === "actif"}
            value={memoryMb} onChange={(e) => setMemoryMb(Number(e.target.value))}
          />
        </div>
        {isAdmin && (
          <div className="flex items-center justify-end px-4 py-3">
            {vm.etat === "actif" && <span className="mr-auto text-xs text-anthracite-500">Arrêtez la VM pour modifier ses ressources.</span>}
            <button className="btn-primary" disabled={!dirty || busy || vm.etat === "actif"} onClick={handleSave}>
              <Save size={13} /> Enregistrer
            </button>
          </div>
        )}
      </div>

      <VMLimitsCard vm={vm} isAdmin={isAdmin} pushToast={pushToast} />
    </div>
  );
}

// Reel : GET/PUT /vms/{name}/limits (cgroups via libvirt, voir
// app/routers/vms.py -- chantier 6). S'applique a chaud ET a froid (pas
// besoin d'arreter la VM, contrairement au redimensionnement vCPU/RAM
// ci-dessus qui touche a la config figee du domaine).
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
    }).catch((e) => pushToast({ kind: "error", title: "Erreur limites", message: e.message }));
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
      pushToast({ kind: "success", title: "Limites appliquées", message: vm.nom });
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card divide-y divide-anthracite-600">
      <div className="flex items-center gap-2 px-4 py-3">
        <Gauge size={14} className="text-anthracite-400" />
        <div className="text-sm font-medium text-anthracite-100">Limites et priorité (cgroups)</div>
      </div>

      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">Priorité CPU (shares)</div>
          <div className="text-xs text-anthracite-400">Relative aux autres VM en cas de contention réelle de l'hôte -- 1024 = normal.</div>
        </div>
        <input type="number" min={2} max={262144} className="input w-28" disabled={!isAdmin}
          value={shares} onChange={(e) => setShares(e.target.value)} />
      </div>

      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">Limite CPU max (% par vCPU)</div>
          <div className="text-xs text-anthracite-400">Plafond dur, même si l'hôte est inactif. Vide = illimité.</div>
        </div>
        <input type="number" min={1} max={100} placeholder="illimité" className="input w-28" disabled={!isAdmin}
          value={cpuLimitPct} onChange={(e) => setCpuLimitPct(e.target.value)} />
      </div>

      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">Limite RAM (Mo)</div>
          <div className="text-xs text-anthracite-400">Plafond dur cgroup, distinct de la RAM allouée ci-dessus. Vide = illimité.</div>
        </div>
        <input type="number" min={64} placeholder="illimité" className="input w-28" disabled={!isAdmin}
          value={memHardLimitMb} onChange={(e) => setMemHardLimitMb(e.target.value)} />
      </div>

      {isAdmin && (
        <div className="flex items-center justify-end px-4 py-3">
          <span className="mr-auto text-xs text-anthracite-500">Consultez l'usage réel dans l'onglet Résumé.</span>
          <button className="btn-primary" disabled={!dirty || busy} onClick={handleSave}>
            <Save size={13} /> Appliquer
          </button>
        </div>
      )}
    </div>
  );
}
