import { useCallback, useEffect, useState } from "react";
import { Server, Plus, Trash2, Copy, Wifi, WifiOff } from "lucide-react";
import {
  fetchRemoteNodes, fetchClusterPubkey, addRemoteNode, fetchRemoteNodeSummary, deleteRemoteNode,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";

// Real: GET/POST/DELETE /nodes (see app/routers/nodes.py): multi-node management
// through qemu+ssh:// (no agent to deploy, see the architecture discussion in
// app/core/cluster.py). The LOCAL host (the one running this Hyperlite instance,
// whichever it is) is managed separately (existing Datacenter tree): this view
// lists the registered REMOTE nodes in addition to it.
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
    fetchRemoteNodes().then(setNodes).catch((e) => pushToast({ kind: "error", title: "Nodes error", message: e.message }));
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
    try { await navigator.clipboard.writeText(pubkey); pushToast({ kind: "success", title: "Key copied", message: "Paste it into authorized_keys on the remote node" }); }
    catch { pushToast({ kind: "error", title: "Copy failed", message: "Copy the key manually" }); }
  }

  async function handleAdd() {
    setBusy(true);
    try {
      await addRemoteNode(form);
      pushToast({ kind: "success", title: "Node added", message: form.name });
      setCreating(false);
      setForm({ name: "", hostname: "", ssh_user: "root", ssh_port: 22 });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Connection failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(node) {
    try {
      await deleteRemoteNode(node.name);
      pushToast({ kind: "success", title: "Node removed", message: node.name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  if (nodes == null) return <div className="card p-4 text-sm text-anthracite-400">Loading...</div>;

  return (
    <div className="space-y-3">
      <p className="text-xs text-anthracite-500 max-w-2xl">
        Direct connection through remote libvirt (qemu+ssh://), with no agent to install on the node: libvirt/QEMU-KVM only has to be running there already and the cluster key below has to be authorized over SSH. This lays the groundwork for future clustering (no vMotion/DRS for now).
      </p>

      {isAdmin && (
        <button className="btn-primary" onClick={() => setCreating((c) => !c)}>
          <Plus size={14} /> Add a remote node
        </button>
      )}

      {creating && (
        <div className="card p-4 space-y-3">
          {pubkey && (
            <div className="rounded-md bg-anthracite-700/60 p-3">
              <div className="text-xs text-anthracite-300 mb-1">
                1. Install this public key in <code>~/.ssh/authorized_keys</code> on the remote node:
              </div>
              <div className="flex items-center gap-2">
                <code className="text-xs text-anthracite-400 truncate flex-1">{pubkey}</code>
                <button className="btn-secondary shrink-0" onClick={handleCopyKey}><Copy size={13} /> Copy</button>
              </div>
            </div>
          )}
          <div className="text-xs text-anthracite-300">2. Enter its connection details:</div>
          <div className="grid grid-cols-4 gap-2">
            <input className="input" placeholder="Name (e.g. node-2)" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input className="input col-span-2" placeholder="IP address or hostname" value={form.hostname} onChange={(e) => setForm((f) => ({ ...f, hostname: e.target.value }))} />
            <input className="input" placeholder="SSH user" value={form.ssh_user} onChange={(e) => setForm((f) => ({ ...f, ssh_user: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn-secondary" onClick={() => setCreating(false)}>Cancel</button>
            <button className="btn-primary" disabled={busy || !form.name || !form.hostname} onClick={handleAdd}>
              {busy ? "Testing connection..." : "Test and add"}
            </button>
          </div>
        </div>
      )}

      <div className="card divide-y divide-anthracite-600">
        {nodes.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No remote node registered: Hyperlite currently manages this host only.</div>}
        {nodes.map((n) => {
          const s = summaries[n.name];
          return (
            <div key={n.id} className="flex items-center gap-3 px-4 py-3 text-sm">
              <Server size={15} className="text-anthracite-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-anthracite-100">{n.name} <span className="text-xs text-anthracite-500">({n.ssh_user}@{n.hostname}:{n.ssh_port})</span></div>
                {s && <div className="text-xs text-anthracite-400">{s.vms_actives} running VM(s), {s.vms_arretees} stopped, {s.stockage_disponible_go ?? "?"} GB free / {s.stockage_capacite_go ?? "?"} GB</div>}
              </div>
              {n.statut === "en_ligne"
                ? <span className="flex items-center gap-1 text-xs text-status-running"><Wifi size={13} /> online</span>
                : <span className="flex items-center gap-1 text-xs text-status-error"><WifiOff size={13} /> offline</span>}
              {isAdmin && <button aria-label="Delete" className="btn-danger" onClick={() => handleDelete(n)}><Trash2 size={13} /></button>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
