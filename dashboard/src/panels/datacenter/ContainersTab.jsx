import { useCallback, useEffect, useState } from "react";
import {
  Box, Plus, Trash2, Play, Square, TerminalSquare, Star, Copy, Archive, RotateCcw,
  Globe, Database, Zap, FileCode, Layers, Search, Check,
} from "lucide-react";
import {
  fetchContainers, createContainer, startContainer, stopContainer, deleteContainer, searchDockerHub,
  cloneContainer, fetchContainerBackups, createContainerBackup, deleteContainerBackup, restoreContainerBackup,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import StatusBadge from "../../components/StatusBadge";
import ConfirmDialog from "../../components/ConfirmDialog";
import { statusLabel } from "../../lib/labels";

// Real: GET/POST/DELETE /containers (see app/routers/containers.py). LXC
// containers through libvirt's native LXC driver, in addition to the existing
// QEMU/KVM VMs. A single debootstrapped Debian 12 base is built when the first
// container is created (cached on the server side). The template gallery is purely
// visual: any Docker Hub/OCI image already worked through the text search (kept
// below for anything that is not in the gallery). Clone and backup/restore exist
// too, with no instantaneous snapshot since libvirt's LXC driver does not support
// it at all (confirmed in testing).
const DEFAULT_FORM = { name: "", vcpu: 1, memory_mb: 512, username: "", password: "", network: "default", image: "" };

// Template gallery: purely visual. Any Docker Hub/OCI image already worked through
// the text search; this only presents the most common ones as cards instead of a
// plain list of search results. The text search remains available below for
// everything else.
const TEMPLATE_GALLERY = [
  { key: "", label: "Debian 12", desc: "Local image, the fastest to create", Icon: Box },
  { key: "ubuntu:24.04", label: "Ubuntu", desc: "General-purpose distribution", Icon: Box },
  { key: "alpine:3.19", label: "Alpine", desc: "Minimal distribution", Icon: Box },
  { key: "nginx:latest", label: "Nginx", desc: "Web server / reverse proxy", Icon: Globe },
  { key: "httpd:latest", label: "Apache", desc: "Web server", Icon: Globe },
  { key: "postgres:16", label: "PostgreSQL", desc: "Relational database", Icon: Database },
  { key: "mysql:8", label: "MySQL", desc: "Relational database", Icon: Database },
  { key: "mariadb:11", label: "MariaDB", desc: "Relational database", Icon: Database },
  { key: "mongo:latest", label: "MongoDB", desc: "Document database", Icon: Database },
  { key: "redis:latest", label: "Redis", desc: "In-memory cache / key-value store", Icon: Zap },
  { key: "node:22", label: "Node.js", desc: "Runtime JavaScript", Icon: FileCode },
  { key: "python:3.12", label: "Python", desc: "Runtime Python", Icon: FileCode },
  { key: "wordpress:latest", label: "WordPress", desc: "CMS", Icon: Layers },
];

export default function ContainersTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [containers, setContainers] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [busy, setBusy] = useState(false);
  const [toDelete, setToDelete] = useState(null);
  const [dockerQuery, setDockerQuery] = useState("");
  const [dockerResults, setDockerResults] = useState([]);
  const [backups, setBackups] = useState(null);

  useEffect(() => {
    if (!dockerQuery.trim()) { setDockerResults([]); return; }
    const id = setTimeout(() => {
      searchDockerHub(dockerQuery).then(setDockerResults).catch(() => setDockerResults([]));
    }, 400);
    return () => clearTimeout(id);
  }, [dockerQuery]);

  const reload = useCallback(() => {
    fetchContainers().then(setContainers).catch((e) => pushToast({ kind: "error", title: "Containers error", message: e.message }));
  }, [pushToast]);
  const reloadBackups = useCallback(() => {
    fetchContainerBackups().then(setBackups).catch(() => setBackups([]));
  }, []);

  useEffect(() => { reload(); reloadBackups(); }, [reload, reloadBackups]);
  useEffect(() => {
    if (!creating) return;
    const id = setInterval(reload, 5000);
    return () => clearInterval(id);
  }, [creating, reload]);

  async function handleCreate() {
    setBusy(true);
    try {
      await createContainer(form);
      pushToast({ kind: "success", title: "Container created", message: `${form.name}: building the system, please wait a moment` });
      setCreating(false);
      setForm(DEFAULT_FORM);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleStart(ct) {
    try { await startContainer(ct.nom); pushToast({ kind: "success", title: "Started", message: ct.nom }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: "Failed", message: e.message }); }
  }
  async function handleStop(ct) {
    try { await stopContainer(ct.nom); pushToast({ kind: "success", title: "Stop requested", message: ct.nom }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: "Failed", message: e.message }); }
  }
  async function handleDelete() {
    if (!toDelete) return;
    try {
      await deleteContainer(toDelete.nom);
      pushToast({ kind: "success", title: "Container deleted", message: toDelete.nom });
      setToDelete(null);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  async function handleClone(ct) {
    const newName = window.prompt(`Name of the copy of '${ct.nom}':`, `${ct.nom}-clone`);
    if (!newName || !newName.trim()) return;
    try {
      await cloneContainer(ct.nom, newName.trim());
      pushToast({ kind: "success", title: "Container cloned", message: `${ct.nom} → ${newName.trim()}` });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Clone failed", message: e.message });
    }
  }

  async function handleBackup(ct) {
    try {
      await createContainerBackup(ct.nom);
      pushToast({ kind: "success", title: "Backup started", message: ct.nom });
      setTimeout(reloadBackups, 2000);
    } catch (e) {
      pushToast({ kind: "error", title: "Backup failed", message: e.message });
    }
  }

  async function handleRestoreBackup(b) {
    const newName = window.prompt(`Restore the backup of '${b.container_name}' under which name?`, `${b.container_name}-restored`);
    if (!newName || !newName.trim()) return;
    try {
      await restoreContainerBackup(b.id, newName.trim());
      pushToast({ kind: "success", title: "Container restored", message: newName.trim() });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Restore failed", message: e.message });
    }
  }

  async function handleDeleteBackup(b) {
    if (!window.confirm(`Permanently delete this backup of '${b.container_name}'?`)) return;
    try {
      await deleteContainerBackup(b.id);
      pushToast({ kind: "success", title: "Backup deleted" });
      await reloadBackups();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  function openTerminal(ct) {
    window.open(`/container-terminal/${encodeURIComponent(ct.nom)}`, `hyperlite-ct-terminal-${ct.nom}`, "width=1000,height=700,noopener");
  }

  if (containers == null) return <div className="card p-4 text-sm text-anthracite-400">Loading...</div>;

  return (
    <div className="space-y-3">
      <p className="text-xs text-anthracite-500 max-w-2xl">
        LXC containers (libvirt's native driver, independent of QEMU/KVM VMs): a minimal Debian 12 system, with terminal access through the same SSH automation key as VMs. The very first creation downloads and prepares the base image (a few minutes); the following ones are fast (local copy).
      </p>

      {isAdmin && (
        <button className="btn-primary" onClick={() => setCreating((c) => !c)}>
          <Plus size={14} /> Create a container
        </button>
      )}

      {creating && (
        <div className="card p-4 space-y-4">
          <div>
            <label className="text-xs font-medium text-anthracite-300 mb-1.5 block">Image</label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
              {TEMPLATE_GALLERY.map((t) => {
                const selected = form.image === t.key;
                return (
                  <button
                    type="button"
                    key={t.label}
                    onClick={() => { setForm((f) => ({ ...f, image: t.key })); setDockerQuery(""); setDockerResults([]); }}
                    className={`relative flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-left transition-colors ${
                      selected ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"
                    }`}
                  >
                    <t.Icon size={16} className={selected ? "text-accent-blue shrink-0 mt-0.5" : "text-anthracite-400 shrink-0 mt-0.5"} />
                    <div className="min-w-0">
                      <div className="text-sm text-anthracite-100 truncate">{t.label}</div>
                      <div className="text-[11px] text-anthracite-500 truncate">{t.desc}</div>
                    </div>
                    {selected && <Check size={13} className="absolute right-2 top-2 text-accent-blue" />}
                  </button>
                );
              })}
            </div>

            <div className="relative mt-2">
              <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-anthracite-500" />
              <input
                className="input w-full pl-8"
                placeholder="Another Docker Hub image: search or type a reference, e.g. traefik, ghcr.io/foo/bar:tag"
                value={dockerQuery}
                onChange={(e) => { setDockerQuery(e.target.value); setForm((f) => ({ ...f, image: e.target.value })); }}
              />
              {dockerResults.length > 0 && (
                <div className="absolute z-10 mt-1 max-h-44 w-full overflow-y-auto rounded-md border border-anthracite-600 bg-anthracite-800 divide-y divide-anthracite-600 shadow-lg">
                  {dockerResults.map((r) => (
                    <button
                      type="button"
                      key={r.nom}
                      onClick={() => { setForm((f) => ({ ...f, image: `${r.nom}:latest` })); setDockerQuery(""); setDockerResults([]); }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-anthracite-700"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 text-sm text-anthracite-100">
                          <span className="truncate">{r.nom}</span>
                          {r.officielle && <span className="shrink-0 rounded bg-accent-blue/20 px-1 text-[10px] text-accent-blue">official</span>}
                        </div>
                        {r.description && <div className="truncate text-xs text-anthracite-400">{r.description}</div>}
                      </div>
                      <div className="flex shrink-0 items-center gap-1 text-xs text-anthracite-500"><Star size={11} /> {r.etoiles}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {form.image && !TEMPLATE_GALLERY.some((t) => t.key === form.image) && (
              <p className="mt-1 text-[11px] text-anthracite-500">Selected image: <span className="text-anthracite-300">{form.image}</span></p>
            )}
          </div>

          <div className="grid grid-cols-4 gap-2">
            <input className="input" placeholder="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input className="input" type="number" min={1} max={16} placeholder="vCPU" value={form.vcpu} onChange={(e) => setForm((f) => ({ ...f, vcpu: Number(e.target.value) }))} />
            <input className="input" type="number" min={128} step={128} placeholder="RAM (MB)" value={form.memory_mb} onChange={(e) => setForm((f) => ({ ...f, memory_mb: Number(e.target.value) }))} />
            <input className="input" placeholder="Network" value={form.network} onChange={(e) => setForm((f) => ({ ...f, network: e.target.value }))} />
            <input className="input" placeholder="User" value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} />
            <input className="input" type="password" placeholder="Password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn-secondary" onClick={() => setCreating(false)}>Cancel</button>
            <button className="btn-primary" disabled={busy || !form.name || !form.username || !form.password} onClick={handleCreate}>
              {busy ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      )}

      <div className="card divide-y divide-anthracite-600">
        {containers.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No containers yet.</div>}
        {containers.map((ct) => (
          <div key={ct.nom} className="flex items-center gap-3 px-4 py-3 text-sm">
            <Box size={15} className="text-anthracite-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-anthracite-100">{ct.nom}</div>
              <div className="text-xs text-anthracite-400">{ct.vcpu} vCPU, {ct.memoire_mo} MB{ct.ip ? ` — ${ct.ip}` : ""}</div>
            </div>
            <StatusBadge etat={ct.etat === "actif" ? "actif" : "arrete"} />
            {isAdmin && ct.etat === "actif" && (
              <button className="btn-secondary" title="Terminal" onClick={() => openTerminal(ct)}><TerminalSquare size={13} /></button>
            )}
            {isAdmin && ct.etat !== "actif" && (
              <button className="btn-secondary" title="Start" onClick={() => handleStart(ct)}><Play size={13} /></button>
            )}
            {isAdmin && ct.etat === "actif" && (
              <button className="btn-secondary" title="Stop" onClick={() => handleStop(ct)}><Square size={13} /></button>
            )}
            {isAdmin && ct.etat !== "actif" && (
              <button className="btn-secondary" title="Clone" onClick={() => handleClone(ct)}><Copy size={13} /></button>
            )}
            {isAdmin && ct.etat !== "actif" && (
              <button className="btn-secondary" title="Back up" onClick={() => handleBackup(ct)}><Archive size={13} /></button>
            )}
            {isAdmin && <button className="btn-danger" title="Delete" onClick={() => setToDelete(ct)}><Trash2 size={13} /></button>}
          </div>
        ))}
      </div>

      {isAdmin && backups && backups.length > 0 && (
        <div className="card">
          <div className="px-4 py-2.5 border-b border-anthracite-600">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-anthracite-100">
              <Archive size={15} /> Container backups
            </h3>
          </div>
          <div className="divide-y divide-anthracite-600">
            {backups.map((b) => (
              <div key={b.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <div className="flex-1 min-w-0">
                  <div className="text-anthracite-100 truncate">{b.container_name}</div>
                  <div className="text-xs text-anthracite-400">
                    {new Date(b.cree_le).toLocaleString()} — {b.taille_octets ? `${(b.taille_octets / 1024 / 1024).toFixed(0)} MB` : "..."} — {statusLabel(b.statut)}
                  </div>
                </div>
                {b.statut === "termine" && (
                  <button className="btn-secondary" title="Restore" onClick={() => handleRestoreBackup(b)}><RotateCcw size={13} /></button>
                )}
                <button className="btn-danger" title="Delete" onClick={() => handleDeleteBackup(b)}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!toDelete}
        title="Delete the container"
        message={`Permanently delete "${toDelete?.nom}" (filesystem included)?`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setToDelete(null)}
      />
    </div>
  );
}
