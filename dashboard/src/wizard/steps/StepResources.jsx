import { Plus, X } from "lucide-react";

// Bornes alignees sur la validation reelle du backend (app/routers/vms.py
// VMCreate: vcpu 1-2, memory_mb 256-2048, 1 a 8 disques de 1 a 500 Go chacun).
export default function StepResources({ form, patch }) {
  function updateDisk(i, size_gb) {
    const disks = form.disks.map((d, idx) => (idx === i ? { size_gb } : d));
    patch({ disks });
  }
  function addDisk() {
    if (form.disks.length >= 8) return;
    patch({ disks: [...form.disks, { size_gb: 5 }] });
  }
  function removeDisk(i) {
    if (form.disks.length <= 1) return;
    patch({ disks: form.disks.filter((_, idx) => idx !== i) });
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs font-medium text-anthracite-300">Nom de la VM</label>
        <input className="input mt-1" value={form.name} onChange={(e) => patch({ name: e.target.value })} placeholder="ex. web-03" />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs font-medium text-anthracite-300">vCPU (1-2)</label>
          <input type="number" min={1} max={2} className="input mt-1" value={form.vcpu} onChange={(e) => patch({ vcpu: Number(e.target.value) })} />
        </div>
        <div>
          <label className="text-xs font-medium text-anthracite-300">Memoire (Mo, 256-2048)</label>
          <input type="number" min={256} max={2048} step={128} className="input mt-1" value={form.memory_mb} onChange={(e) => patch({ memory_mb: Number(e.target.value) })} />
        </div>
      </div>

      <div>
        <label className="text-xs font-medium text-anthracite-300">Disques (Go)</label>
        <div className="mt-1 space-y-1.5">
          {form.disks.map((d, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-10 font-mono text-xs text-anthracite-400">sd{String.fromCharCode(97 + i)}</span>
              <input type="number" min={1} max={500} className="input" value={d.size_gb} onChange={(e) => updateDisk(i, Number(e.target.value))} />
              <button className="btn-secondary px-2" disabled={form.disks.length <= 1} onClick={() => removeDisk(i)}><X size={13} /></button>
            </div>
          ))}
        </div>
        <button className="btn-secondary mt-2" onClick={addDisk} disabled={form.disks.length >= 8}>
          <Plus size={13} /> Ajouter un disque
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs font-medium text-anthracite-300">Utilisateur</label>
          <input className="input mt-1" value={form.username} onChange={(e) => patch({ username: e.target.value })} placeholder="ex. antho" />
        </div>
        <div>
          <label className="text-xs font-medium text-anthracite-300">Mot de passe</label>
          <input type="password" className="input mt-1" value={form.password} onChange={(e) => patch({ password: e.target.value })} minLength={4} />
        </div>
      </div>
    </div>
  );
}
