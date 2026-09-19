import { Plus, X } from "lucide-react";
import { detectOsFamily } from "../../utils/osFamily";
import { useHostLimits } from "../../hooks/useHostLimits";
import OverallocationNote from "../../components/OverallocationNote";

// Bornes DERIVEES de l'hote reel via GET /host/limits (mandat portabilite,
// chantier 2) -- plus de plafond fige a 2 vCPU/2 Go.
// Le compte utilisateur reste necessaire sans ISO (cloud-init) ET avec un ISO
// reconnu (installation automatisee, meme logique que detect_os_family cote
// backend) -- seule l'installation manuelle (ISO non reconnu) s'en passe.
export default function StepResources({ form, patch, storagePools = [] }) {
  const limits = useHostLimits();
  // Choix du pool de stockage (backlog 2026-09-18) : dir/netfs (chemin de
  // fichiers qcow2 classique) ou zfs (zvols bruts, backlog stockage
  // 2026-09-18, voir app/routers/vms.py::create_vm) -- et seulement les
  // pools ACTIFS, un pool inactif ferait echouer la creation de la VM.
  const selectablePools = storagePools.filter((p) => ["dir", "netfs", "zfs"].includes(p.type) && p.etat === "actif");
  const manualInstall = Boolean(form.iso) && !detectOsFamily(form.iso);
  const importMode = form.importDisk != null;
  function updateDisk(i, size_gb) {
    const disks = form.disks.map((d, idx) => (idx === i ? { size_gb } : d));
    patch({ disks });
  }
  function addDisk() {
    if (limits && form.disks.length >= limits.disques.max) return;
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
          <label className="text-xs font-medium text-anthracite-300">vCPU{limits ? ` (${limits.vcpu.min}-${limits.vcpu.max})` : ""}</label>
          <input type="number" min={limits?.vcpu.min ?? 1} max={limits?.vcpu.max} className="input mt-1" value={form.vcpu} onChange={(e) => patch({ vcpu: Number(e.target.value) })} />
        </div>
        <div>
          <label className="text-xs font-medium text-anthracite-300">Mémoire (Mo{limits ? `, ${limits.memoire_mo.min}-${limits.memoire_mo.max}` : ""})</label>
          <input type="number" min={limits?.memoire_mo.min ?? 256} max={limits?.memoire_mo.max} step={128} className="input mt-1" value={form.memory_mb} onChange={(e) => patch({ memory_mb: Number(e.target.value) })} />
        </div>
      </div>
      <OverallocationNote limits={limits} vcpu={form.vcpu} memoryMb={form.memory_mb} diskGb={Math.max(0, ...form.disks.map((d) => d.size_gb || 0))} />

      <div>
        <label className="text-xs font-medium text-anthracite-300">Disques (Go)</label>
        <div className="mt-1 space-y-1.5">
          {form.disks.map((d, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-10 font-mono text-xs text-anthracite-400">sd{String.fromCharCode(97 + i)}</span>
              {importMode && i === 0 ? (
                <span className="input flex items-center text-anthracite-500">Taille du disque importé (ignoré)</span>
              ) : (
                <input type="number" min={1} max={limits?.disque_go.max} className="input" value={d.size_gb} onChange={(e) => updateDisk(i, Number(e.target.value))} />
              )}
              <button className="btn-secondary px-2" disabled={form.disks.length <= 1 || (importMode && i === 0)} onClick={() => removeDisk(i)}><X size={13} /></button>
            </div>
          ))}
        </div>
        <button className="btn-secondary mt-2" onClick={addDisk} disabled={Boolean(limits) && form.disks.length >= limits.disques.max}>
          <Plus size={13} /> Ajouter un disque
        </button>
      </div>

      {selectablePools.length > 0 && (
        <div>
          <label className="text-xs font-medium text-anthracite-300">Pool de stockage</label>
          <select className="input mt-1" value={form.storagePool} onChange={(e) => patch({ storagePool: e.target.value })}>
            <option value="">Par défaut (local)</option>
            {selectablePools.map((p) => (
              <option key={p.nom} value={p.nom}>{p.nom} ({p.type}, {p.disponible_go} Go libres)</option>
            ))}
          </select>
          <p className="mt-1 text-[11px] text-anthracite-500">
            Choisir un pool de stockage réseau (netfs) partagé permet ensuite de protéger cette VM en HA ou de la migrer à chaud.
          </p>
        </div>
      )}

      {importMode ? (
        <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-300">
          Compte utilisateur non applicable : le disque importé a déjà son propre OS et ses propres comptes (voir l'étape "Modèle").
        </div>
      ) : manualInstall ? (
        <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-300">
          Compte utilisateur non applicable : cet ISO n'est pas reconnu pour l'installation automatisée, l'OS et son compte seront créés pendant l'installation manuelle (voir l'étape "Modèle").
        </div>
      ) : (
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
      )}

      <div className="rounded-md border border-anthracite-600 px-3 py-2.5">
        <label className="flex items-center gap-2 text-sm text-anthracite-200">
          <input
            type="checkbox" checked={form.autoCleanupEnabled}
            onChange={(e) => patch({ autoCleanupEnabled: e.target.checked })}
          />
          Supprimer automatiquement cette VM si elle reste arrêtée trop longtemps
        </label>
        {form.autoCleanupEnabled && (
          <div className="mt-2 flex items-center gap-2 text-sm text-anthracite-300">
            Après
            <input
              type="number" min={1} max={365} className="input w-20"
              value={form.autoCleanupDays} onChange={(e) => patch({ autoCleanupDays: Number(e.target.value) })}
            />
            jour(s) d'arrêt continu. Une VM en marche n'est jamais concernée, et une alerte est envoyée ~24h avant la suppression.
          </div>
        )}
      </div>
    </div>
  );
}
