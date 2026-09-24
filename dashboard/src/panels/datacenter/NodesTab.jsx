import LoadingState from "../../components/LoadingState";
import { confirmAction } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { Server, Plus, Trash2, Copy, Wifi, WifiOff } from "lucide-react";
import {
  fetchRemoteNodes, fetchClusterPubkey, addRemoteNode, fetchRemoteNodeSummary, deleteRemoteNode,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

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
    if (!(await confirmAction({ title: `Remove node '${node.name}'?`, message: "Hyperlite stops managing this node. Its VMs are not deleted.", confirmLabel: "Remove" }))) return;
    try {
      await deleteRemoteNode(node.name);
      pushToast({ kind: "success", title: "Node removed", message: node.name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  if (nodes == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground max-w-2xl">
        Direct connection through remote libvirt (qemu+ssh://), with no agent to install on the node: libvirt/QEMU-KVM only has to be running there already and the cluster key below has to be authorized over SSH. This lays the groundwork for future clustering (no vMotion/DRS for now).
      </p>

      {isAdmin && (
        <Button onClick={() => setCreating((c) => !c)}>
          <Plus /> Add a remote node
        </Button>
      )}

      {creating && (
        <Card className="p-4 space-y-3 animate-in fade-in-0 slide-in-from-top-1 duration-150">
          {pubkey && (
            <div className="rounded-md bg-muted/60 p-3">
              <div className="text-xs text-foreground/80 mb-1">
                1. Install this public key in <code>~/.ssh/authorized_keys</code> on the remote node:
              </div>
              <div className="flex items-center gap-2">
                <code className="text-xs text-muted-foreground truncate flex-1">{pubkey}</code>
                <Button variant="secondary" size="sm" className="shrink-0" onClick={handleCopyKey}><Copy /> Copy</Button>
              </div>
            </div>
          )}
          <div className="text-xs text-foreground/80">2. Enter its connection details:</div>
          <div className="grid grid-cols-4 gap-2">
            <Input aria-label="Name (e.g. node-2)" placeholder="Name (e.g. node-2)" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <Input aria-label="IP address or hostname" className="col-span-2" placeholder="IP address or hostname" value={form.hostname} onChange={(e) => setForm((f) => ({ ...f, hostname: e.target.value }))} />
            <Input aria-label="SSH user" placeholder="SSH user" value={form.ssh_user} onChange={(e) => setForm((f) => ({ ...f, ssh_user: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCreating(false)}>Cancel</Button>
            <Button disabled={busy || !form.name || !form.hostname} onClick={handleAdd}>
              {busy ? "Testing connection..." : "Test and add"}
            </Button>
          </div>
        </Card>
      )}

      <Card className="p-0 divide-y divide-border">
        {nodes.length === 0 && <div className="px-4 py-3 text-sm text-muted-foreground">No remote node registered: Hyperlite currently manages this host only.</div>}
        {nodes.map((n) => {
          const s = summaries[n.name];
          return (
            <div key={n.id} className="flex items-center gap-3 px-4 py-3 text-sm transition-colors duration-150 hover:bg-muted/40">
              <Server size={15} className="text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-foreground">{n.name} <span className="text-xs text-muted-foreground">({n.ssh_user}@{n.hostname}:{n.ssh_port})</span></div>
                {s && <div className="text-xs text-muted-foreground">{s.vms_actives} running VM(s), {s.vms_arretees} stopped, {s.stockage_disponible_go ?? "?"} GB free / {s.stockage_capacite_go ?? "?"} GB</div>}
              </div>
              {n.statut === "en_ligne"
                ? <span className="flex items-center gap-1 text-xs text-status-running"><Wifi size={13} /> online</span>
                : <span className="flex items-center gap-1 text-xs text-status-error"><WifiOff size={13} /> offline</span>}
              {isAdmin && <Button aria-label={`Remove node ${n.name}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => handleDelete(n)}><Trash2 size={13} /></Button>}
            </div>
          );
        })}
      </Card>
    </div>
  );
}
