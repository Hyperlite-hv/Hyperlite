import { confirmAction } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { Trash2, Plus, HardDrive, Network, Layers } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";
import { fetchIsoTemplates, deleteIso, createStoragePool, deleteStoragePool } from "../../api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// "zfs": a ZFS pool managed outside libvirt (see app/core/zfs_storage.py), backed
// for now by a loopback file (size_gb). It is single-node and always created on
// the local host: the node selector is ignored on the backend side for this type.
const EMPTY_FORM = { name: "", type: "dir", node: "local", path: "", nfs_host: "", nfs_export_path: "", size_gb: "20" };

const POOL_TYPES = [
  { value: "dir", label: "Local directory", Icon: HardDrive },
  { value: "netfs", label: "NFS share", Icon: Network },
  { value: "zfs", label: "ZFS", Icon: Layers },
];

// Pools: an aggregated view of GET /storage across all nodes. ISO images: the real
// list/upload/deletion through GET/POST/DELETE /isos. Pool creation/deletion
// (shared NFS storage) is real, through POST/DELETE /storage
// (app/routers/storage.py).
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
    catch (e) { pushToast({ kind: "error", title: "ISO error", message: e.message }); }
  }, [pushToast]);

  useEffect(() => { reloadIsos(); }, [reloadIsos]);

  async function handleDelete(nom) {
    if (!(await confirmAction({ title: `Delete ISO '${nom}'?`, message: "The file is permanently deleted from the server.", confirmLabel: "Delete" }))) return;
    try {
      await deleteIso(nom);
      pushToast({ kind: "success", title: "ISO deleted", message: nom });
      reloadIsos();
    } catch (e) {
      pushToast({ kind: "error", title: "Deletion failed", message: e.message });
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
      // node "local" = the local host (see the fetchNodes()/open_conn convention): the
      // backend only accepts the name of a registered remote node, never "local" itself.
      const node = form.node === "local" ? undefined : form.node;
      await createStoragePool(payload, node);
      pushToast({ kind: "success", title: "Pool created", message: form.name });
      setForm(EMPTY_FORM);
      setFormOpen(false);
      refreshAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleDeletePool(pool) {
    if (pool.nom === "default") return;
    // dir/NFS pools: only the pool DEFINITION is removed, never the files (the server
    // refuses if a VM uses them). Other types: the pool must be empty, the historical
    // behaviour.
    const fsBacked = pool.type === "dir" || pool.type === "netfs";
    const msg = fsBacked
      ? `Remove the pool '${pool.nom}' from Hyperlite?\n\nIts files are NOT deleted (only the pool definition goes away).`
      : `Delete the pool '${pool.nom}'? The pool must be empty.`;
    if (!(await confirmAction({ title: "Please confirm", message: msg, confirmLabel: "Confirm" }))) return;
    try {
      await deleteStoragePool(pool.nom, pool.node === "local" ? undefined : pool.node, fsBacked);
      pushToast({ kind: "success", title: "Pool removed", message: pool.nom });
      refreshAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Deletion failed", message: e.message });
    }
  }

  return (
    <div className="space-y-5">
      <Card className="p-0">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <h3 className="text-sm font-semibold text-foreground">Storage pools</h3>
          {isAdmin && (
            <Button variant="secondary" onClick={() => setFormOpen((o) => !o)}>
              <Plus /> Create a pool
            </Button>
          )}
        </div>

        {formOpen && (
          <form onSubmit={handleCreatePool} className="space-y-3 border-b border-border px-4 py-4 animate-in fade-in-0 slide-in-from-top-1 duration-150">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs font-medium text-foreground/80">Pool name</Label>
                <Input aria-label="Pool name" className="mt-1" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. nfs-shared" />
              </div>
              <div>
                <Label className="text-xs font-medium text-foreground/80">Node</Label>
                <Select value={form.node} onValueChange={(v) => setForm({ ...form, node: v })}>
                  <SelectTrigger aria-label="Node" className="mt-1 w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {nodes.map((n) => <SelectItem key={n.id} value={n.id}>{n.nom}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="flex gap-2">
              {POOL_TYPES.map(({ value, label, Icon }) => (
                <Button
                  key={value}
                  type="button"
                  variant={form.type === value ? "default" : "outline"}
                  className={`flex-1 ${form.type === value ? "" : "text-foreground/80"}`}
                  onClick={() => setForm({ ...form, type: value })}
                >
                  <Icon /> {label}
                </Button>
              ))}
            </div>

            {form.type === "dir" ? (
              <div>
                <Label className="text-xs font-medium text-foreground/80">Local path (optional)</Label>
                <Input aria-label="Local path (optional)" className="mt-1" value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} placeholder="/var/lib/libvirt/hyperlite-pools/... (automatic if empty)" />
              </div>
            ) : form.type === "netfs" ? (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs font-medium text-foreground/80">NFS server host</Label>
                  <Input aria-label="NFS server host" className="mt-1" required value={form.nfs_host} onChange={(e) => setForm({ ...form, nfs_host: e.target.value })} placeholder="e.g. 192.168.1.10" />
                </div>
                <div>
                  <Label className="text-xs font-medium text-foreground/80">Exported path</Label>
                  <Input aria-label="Exported path" className="mt-1" required value={form.nfs_export_path} onChange={(e) => setForm({ ...form, nfs_export_path: e.target.value })} placeholder="/srv/share" />
                </div>
              </div>
            ) : (
              <div>
                <Label className="text-xs font-medium text-foreground/80">Size (GB, loopback file)</Label>
                <Input aria-label="Size (GB, loopback file)" type="number" min="1" max="4096" className="mt-1" required value={form.size_gb} onChange={(e) => setForm({ ...form, size_gb: e.target.value })} />
                <p className="mt-1 text-xs text-muted-foreground">ZFS pool created on the local host only, backed by a file. VMs created on it use raw block disks (zvols).</p>
              </div>
            )}

            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setFormOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={busy}>{busy ? "Creating..." : "Create"}</Button>
            </div>
          </form>
        )}

        <div className="divide-y divide-border">
          <div className="grid grid-cols-6 gap-2 px-4 py-2 text-xs font-medium text-muted-foreground">
            <span>Pool</span><span>Node</span><span>Type</span><span>Capacity</span><span>Available</span><span />
          </div>
          {storagePools.map((p) => (
            <div key={`${p.node}-${p.nom}`} className="grid grid-cols-6 gap-2 px-4 py-2.5 text-sm items-center transition-colors duration-150 hover:bg-muted/40">
              <span className="text-foreground">{p.nom}</span>
              <span className="text-foreground/80">{p.node}</span>
              <span className="text-foreground/80">{p.type === "netfs" ? "NFS" : p.type === "zfs" ? "ZFS" : p.type}</span>
              <span className="text-foreground/80">{p.capacite_go} GB</span>
              <span className="text-foreground/80">{p.disponible_go} GB</span>
              <span className="text-right">
                {isAdmin && p.nom !== "default" && (
                  <Button aria-label={`Delete pool ${p.nom}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => handleDeletePool(p)}><Trash2 size={13} /></Button>
                )}
              </span>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-5">
        <h3 className="mb-3 text-sm font-semibold text-foreground">ISO images</h3>
        {isAdmin && <IsoUploadDropzone onDone={reloadIsos} />}
        <div className="mt-4 divide-y divide-border">
          {(isos || []).length === 0 && isos != null && <div className="py-3 text-sm text-muted-foreground">No ISO.</div>}
          {(isos || []).map((iso) => (
            <div key={iso.nom} className="flex items-center gap-3 py-2.5 text-sm transition-colors duration-150 hover:bg-muted/40">
              <span className="text-foreground flex-1 truncate">{iso.nom}</span>
              <span className="text-muted-foreground text-xs">{iso.taille_mo} MB</span>
              {isAdmin && (
                <Button aria-label={`Delete ISO ${iso.nom}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => handleDelete(iso.nom)}><Trash2 size={13} /></Button>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
