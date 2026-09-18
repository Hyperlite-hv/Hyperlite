import { useCallback, useEffect, useState } from "react";
import { Server, Plus, Trash2, Copy, Wifi, WifiOff } from "lucide-react";
import {
  fetchRemoteNodes, fetchClusterPubkey, addRemoteNode, fetchRemoteNodeSummary, deleteRemoteNode,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";

// Reel : GET/POST/DELETE /nodes (voir app/routers/nodes.py, chantier 15) --
// gestion multi-noeuds via qemu+ssh:// (pas d'agent a deployer, voir la
// discussion d'architecture dans app/core/cluster.py). L'hôte LOCAL (celui
// qui fait tourner cette instance Hyperlite, quel qu'il soit -- kvm-lab à
// l'origine, démantelé le 2026-09-18, potentiellement serveur-antho ou un
// autre aujourd'hui) reste géré séparément (arborescence Datacenter
// existante) -- cette vue liste les nœuds DISTANTS enregistrés en plus de lui.
export default function NodesTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [nodes, setNodes] = useState(null);
  const [pubkey, setPubkey] = useState(null);
  const [summaries, setSummaries] = useState({});
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", hostname: "", ssh_user: "root", ssh_port: 22 });
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    fetchRemoteNodes().then(setNodes).catch((e) => pushToast({ kind: "error", title: "Erreur nœuds", message: e.message }));
  }, [pushToast]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (isAdmin) fetchClusterPubkey().then((r) => setPubkey(r.public_key)).catch(() => {});
  }, [isAdmin]);

  useEffect(() => {
    if (!nodes) return;
    nodes.forEach((n) => {
      fetchRemoteNodeSummary(n.name).then((s) => setSummaries((prev) => ({ ...prev, [n.name]: s })))
        .catch(() => setSummaries((prev) => ({ ...prev, [n.name]: null })));
    });
  }, [nodes]);

  async function handleCopyKey() {
    try { await navigator.clipboard.writeText(pubkey); pushToast({ kind: "success", title: "Clé copiée", message: "Collez-la dans authorized_keys du nœud distant" }); }
    catch (e) { pushToast({ kind: "error", title: "Copie impossible", message: "Copiez la clé manuellement" }); }
  }

  async function handleAdd() {
    setBusy(true);
    try {
      await addRemoteNode(form);
      pushToast({ kind: "success", title: "Nœud ajouté", message: form.name });
      setCreating(false);
      setForm({ name: "", hostname: "", ssh_user: "root", ssh_port: 22 });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la connexion", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(node) {
    try {
      await deleteRemoteNode(node.name);
      pushToast({ kind: "success", title: "Nœud retiré", message: node.name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    }
  }

  if (nodes == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  return (
    <div className="space-y-3">
      <p className="text-xs text-anthracite-500 max-w-2xl">
        Connexion directe via libvirt distant (qemu+ssh://), sans agent à installer sur le nœud —
        il suffit que libvirt/QEMU-KVM y tourne déjà et que la clé du cluster ci-dessous soit autorisée en SSH.
        Pose les bases d'un futur clustering (pas de vMotion/DRS pour l'instant).
      </p>

      {isAdmin && (
        <button className="btn-primary" onClick={() => setCreating((c) => !c)}>
          <Plus size={14} /> Ajouter un nœud distant
        </button>
      )}

      {creating && (
        <div className="card p-4 space-y-3">
          {pubkey && (
            <div className="rounded-md bg-anthracite-700/60 p-3">
              <div className="text-xs text-anthracite-300 mb-1">
                1. Installez cette clé publique dans <code>~/.ssh/authorized_keys</code> sur le nœud distant :
              </div>
              <div className="flex items-center gap-2">
                <code className="text-xs text-anthracite-400 truncate flex-1">{pubkey}</code>
                <button className="btn-secondary shrink-0" onClick={handleCopyKey}><Copy size={13} /> Copier</button>
              </div>
            </div>
          )}
          <div className="text-xs text-anthracite-300">2. Renseignez sa connexion :</div>
          <div className="grid grid-cols-4 gap-2">
            <input className="input" placeholder="Nom (ex. node-2)" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input className="input col-span-2" placeholder="Adresse IP ou nom d'hôte" value={form.hostname} onChange={(e) => setForm((f) => ({ ...f, hostname: e.target.value }))} />
            <input className="input" placeholder="Utilisateur SSH" value={form.ssh_user} onChange={(e) => setForm((f) => ({ ...f, ssh_user: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn-secondary" onClick={() => setCreating(false)}>Annuler</button>
            <button className="btn-primary" disabled={busy || !form.name || !form.hostname} onClick={handleAdd}>
              {busy ? "Test de connexion..." : "Tester et ajouter"}
            </button>
          </div>
        </div>
      )}

      <div className="card divide-y divide-anthracite-600">
        {nodes.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucun nœud distant enregistré — Hyperlite pilote uniquement cet hôte pour l'instant.</div>}
        {nodes.map((n) => {
          const s = summaries[n.name];
          return (
            <div key={n.id} className="flex items-center gap-3 px-4 py-3 text-sm">
              <Server size={15} className="text-anthracite-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-anthracite-100">{n.name} <span className="text-xs text-anthracite-500">({n.ssh_user}@{n.hostname}:{n.ssh_port})</span></div>
                {s && <div className="text-xs text-anthracite-400">{s.vms_actives} VM active(s), {s.vms_arretees} arrêtée(s) — {s.stockage_disponible_go ?? "?"} Go libres / {s.stockage_capacite_go ?? "?"} Go</div>}
              </div>
              {n.statut === "en_ligne"
                ? <span className="flex items-center gap-1 text-xs text-status-running"><Wifi size={13} /> en ligne</span>
                : <span className="flex items-center gap-1 text-xs text-status-error"><WifiOff size={13} /> hors ligne</span>}
              {isAdmin && <button className="btn-danger" onClick={() => handleDelete(n)}><Trash2 size={13} /></button>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
