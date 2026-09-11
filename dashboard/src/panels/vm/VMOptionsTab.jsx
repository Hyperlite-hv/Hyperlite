import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";

// Reel : PATCH /vms/{name} (ajoute pour permettre ce qui manquait le plus --
// changer vCPU/RAM d'une VM existante sans devoir la recreer). Necessite la
// VM arretee, comme cote Proxmox pour un redimensionnement hors-ligne.
export default function VMOptionsTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const updateVMResources = useInfraStore((s) => s.updateVMResources);
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
    <div className="card divide-y divide-anthracite-600">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="text-sm text-anthracite-100">Nom d'hote</div>
        <div className="text-sm text-anthracite-300 font-mono">{vm.nom}</div>
      </div>
      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">vCPU</div>
          <div className="text-xs text-anthracite-400">1 a 2 -- VM arretee requise</div>
        </div>
        <input
          type="number" min={1} max={2} className="input w-24" disabled={!isAdmin || vm.etat === "actif"}
          value={vcpu} onChange={(e) => setVcpu(Number(e.target.value))}
        />
      </div>
      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="text-sm text-anthracite-100">Memoire (Mo)</div>
          <div className="text-xs text-anthracite-400">256 a 2048 -- VM arretee requise</div>
        </div>
        <input
          type="number" min={256} max={2048} step={128} className="input w-24" disabled={!isAdmin || vm.etat === "actif"}
          value={memoryMb} onChange={(e) => setMemoryMb(Number(e.target.value))}
        />
      </div>
      {isAdmin && (
        <div className="flex items-center justify-end px-4 py-3">
          {vm.etat === "actif" && <span className="mr-auto text-xs text-anthracite-500">Arretez la VM pour modifier ses ressources.</span>}
          <button className="btn-primary" disabled={!dirty || busy || vm.etat === "actif"} onClick={handleSave}>
            <Save size={13} /> Enregistrer
          </button>
        </div>
      )}
    </div>
  );
}
