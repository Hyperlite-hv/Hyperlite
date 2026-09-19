import { useCallback, useEffect, useState } from "react";
import { Trash2, Plus, HardDrive, Network, Layers } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";
import { fetchIsoTemplates, deleteIso, createStoragePool, deleteStoragePool } from "../../api/client";

// "zfs" (backlog stockage 2026-09-18) : pool ZFS gere hors libvirt (voir
// app/core/zfs_storage.py), adosse pour l'instant a un fichier loopback
// (size_gb) -- mono-nœud, toujours cree sur l'hote local (kvm-lab), le
// selecteur de nœud est ignoré côté backend pour ce type.
const EMPTY_FORM = { name: "", type: "dir", node: "local", path: "", nfs_host: "", nfs_export_path: "", size_gb: "20" };

// Pools : vue agregee de GET /storage sur tous les noeuds -- reel pour kvm-lab.
// Images ISO : vraie liste/upload/suppression via GET/POST/DELETE /isos.
// Creation/suppression de pool (chantier 26, stockage partage NFS) : reel,
// via POST/DELETE /storage (app/routers/storage.py).
export default function StorageTab() {
  const storagePools = useInfraStore((s) => s.storagePools);
  const nodes = useInfraStore((s) => s.nodes);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const pushToast = useInfraStore((s) => s.pushToast);
  const isAdmin = useAuthStore(selectIsAdmin);
  const [isos, setIsos] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);

  const reloadIsos = useCallback(async () => {
    try { setIsos(await fetchIsoTemplates()); }
    catch (e) { pushToast({ kind: "error", title: "Erreur ISO", message: e.message }); }
  }, [pushToast]);

  useEffect(() => { reloadIsos(); }, [reloadIsos]);

  async function handleDelete(nom) {
    try {
      await deleteIso(nom);
      pushToast({ kind: "success", title: "ISO supprimée", message: nom });
      reloadIsos();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la suppression", message: e.message });
    }
  }

  async function handleCreatePool(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = form.type === "dir"
        ? { name: form.name, type: "dir", path: form.path || null }
        : form.type === "netfs"
        ? { name: form.name, type: "netfs", nfs_host: form.nfs_host, nfs_export_path: form.nfs_export_path }
        : { name: form.name, type: "zfs", size_gb: Number(form.size_gb) };
      // node "local" = hote local (voir convention fetchNodes()/open_conn) :
      // le backend n'accepte que le nom d'un nœud distant enregistré, jamais
      // "local" lui-même.
      const node = form.node === "local" ? undefined : form.node;
      await createStoragePool(payload, node);
      pushToast({ kind: "success", title: "Pool créé", message: form.name });
      setForm(EMPTY_FORM);
      setFormOpen(false);
      refreshAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la création", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleDeletePool(pool) {
    if (pool.nom === "default") return;
    // Pools dir/NFS : on ne retire que la DEFINITION du pool, jamais les
    // fichiers (le serveur refuse si une VM en utilise). Autres types : le
    // pool doit etre vide, comportement historique.
    const fsBacked = pool.type === "dir" || pool.type === "netfs";
    const msg = fsBacked
      ? `Retirer le pool '${pool.nom}' d'Hyperlite ?\n\nSes fichiers ne sont PAS supprimés (seule la définition du pool disparaît).`
      : `Supprimer le pool '${pool.nom}' ? Le pool doit être vide.`;
    if (!window.confirm(msg)) return;
    try {
      await deleteStoragePool(pool.nom, pool.node === "local" ? undefined : pool.node, fsBacked);
      pushToast({ kind: "success", title: "Pool retiré", message: pool.nom });
      refreshAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la suppression", message: e.message });
    }
  }

  return (
    <div className="space-y-5">
      <div className="card">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-anthracite-600">
          <h3 className="text-sm font-semibold text-anthracite-100">Pools de stockage</h3>
          {isAdmin && (
            <button className="btn-secondary" onClick={() => setFormOpen((o) => !o)}>
              <Plus size={14} /> Créer un pool
            </button>
          )}
        </div>

        {formOpen && (
          <form onSubmit={handleCreatePool} className="space-y-3 border-b border-anthracite-600 px-4 py-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-anthracite-300">Nom du pool</label>
                <input className="input mt-1" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="ex. nfs-partage" />
              </div>
              <div>
                <label className="text-xs font-medium text-anthracite-300">Nœud</label>
                <select className="input mt-1" value={form.node} onChange={(e) => setForm({ ...form, node: e.target.value })}>
                  {nodes.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
                </select>
              </div>
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setForm({ ...form, type: "dir" })}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium ${form.type === "dir" ? "border-accent-blue bg-accent-blue/10 text-accent-blue" : "border-anthracite-600 text-anthracite-300"}`}
              >
                <HardDrive size={14} /> Répertoire local
              </button>
              <button
                type="button"
                onClick={() => setForm({ ...form, type: "netfs" })}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium ${form.type === "netfs" ? "border-accent-blue bg-accent-blue/10 text-accent-blue" : "border-anthracite-600 text-anthracite-300"}`}
              >
                <Network size={14} /> Partage NFS
              </button>
              <button
                type="button"
                onClick={() => setForm({ ...form, type: "zfs" })}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium ${form.type === "zfs" ? "border-accent-blue bg-accent-blue/10 text-accent-blue" : "border-anthracite-600 text-anthracite-300"}`}
              >
                <Layers size={14} /> ZFS
              </button>
            </div>

            {form.type === "dir" ? (
              <div>
                <label className="text-xs font-medium text-anthracite-300">Chemin local (optionnel)</label>
                <input className="input mt-1" value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} placeholder="/var/lib/libvirt/hyperlite-pools/... (auto si vide)" />
              </div>
            ) : form.type === "netfs" ? (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-anthracite-300">Hôte du serveur NFS</label>
                  <input className="input mt-1" required value={form.nfs_host} onChange={(e) => setForm({ ...form, nfs_host: e.target.value })} placeholder="ex. 192.168.1.10" />
                </div>
                <div>
                  <label className="text-xs font-medium text-anthracite-300">Chemin exporté</label>
                  <input className="input mt-1" required value={form.nfs_export_path} onChange={(e) => setForm({ ...form, nfs_export_path: e.target.value })} placeholder="/srv/partage" />
                </div>
              </div>
            ) : (
              <div>
                <label className="text-xs font-medium text-anthracite-300">Taille (Go, fichier loopback)</label>
                <input type="number" min="1" max="4096" className="input mt-1" required value={form.size_gb} onChange={(e) => setForm({ ...form, size_gb: e.target.value })} />
                <p className="mt-1 text-xs text-anthracite-400">Pool ZFS créé sur l'hôte local uniquement, adossé à un fichier — les VM créées dessus utilisent des disques bloc bruts (zvols).</p>
              </div>
            )}

            <div className="flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>Annuler</button>
              <button type="submit" disabled={busy} className="btn-primary">{busy ? "Création..." : "Créer"}</button>
            </div>
          </form>
        )}

        <div className="divide-y divide-anthracite-600">
          <div className="grid grid-cols-6 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
            <span>Pool</span><span>Nœud</span><span>Type</span><span>Capacité</span><span>Disponible</span><span />
          </div>
          {storagePools.map((p) => (
            <div key={`${p.node}-${p.nom}`} className="grid grid-cols-6 gap-2 px-4 py-2.5 text-sm items-center">
              <span className="text-anthracite-100">{p.nom}</span>
              <span className="text-anthracite-300">{p.node}</span>
              <span className="text-anthracite-300">{p.type === "netfs" ? "NFS" : p.type === "zfs" ? "ZFS" : p.type}</span>
              <span className="text-anthracite-300">{p.capacite_go} Go</span>
              <span className="text-anthracite-300">{p.disponible_go} Go</span>
              <span className="text-right">
                {isAdmin && p.nom !== "default" && (
                  <button className="btn-danger" onClick={() => handleDeletePool(p)}><Trash2 size={13} /></button>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Images ISO</h3>
        {isAdmin && <IsoUploadDropzone onDone={reloadIsos} />}
        <div className="mt-4 divide-y divide-anthracite-600">
          {(isos || []).length === 0 && isos != null && <div className="py-3 text-sm text-anthracite-400">Aucune ISO.</div>}
          {(isos || []).map((iso) => (
            <div key={iso.nom} className="flex items-center gap-3 py-2.5 text-sm">
              <span className="text-anthracite-100 flex-1 truncate">{iso.nom}</span>
              <span className="text-anthracite-400 text-xs">{iso.taille_mo} Mo</span>
              {isAdmin && (
                <button className="btn-danger" onClick={() => handleDelete(iso.nom)}><Trash2 size={13} /></button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
