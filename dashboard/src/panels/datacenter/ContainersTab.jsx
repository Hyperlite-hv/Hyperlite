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

// Reel : GET/POST/DELETE /containers (voir app/routers/containers.py,
// chantier 18) -- conteneurs LXC via le pilote LXC natif de libvirt, en
// plus des VM QEMU/KVM existantes. Une seule base Debian 12
// debootstrappee au premier conteneur cree (mise en cache cote serveur).
// Galerie de templates ajoutee en backlog (2026-09-18) : purement
// visuelle, n'importe quelle image Docker Hub/OCI fonctionnait deja via
// la recherche texte (conservee en dessous pour tout ce qui n'est pas
// dans la galerie). Clonage + sauvegarde/
// restauration ajoutes en backlog (2026-09-18) -- pas de snapshot
// instantane possible, le pilote LXC de libvirt ne le supporte pas du
// tout (confirme en testant).
const DEFAULT_FORM = { name: "", vcpu: 1, memory_mb: 512, username: "", password: "", network: "default", image: "" };

// Galerie de templates (backlog 2026-09-18, "idée notée" du chantier 18 --
// purement visuel : n'importe quelle image Docker Hub/OCI fonctionnait déjà
// via la recherche texte, ceci ne fait que présenter les plus courantes
// sous forme de cartes plutôt qu'une simple liste de résultats de recherche.
// La recherche texte reste disponible en dessous pour tout le reste.
const TEMPLATE_GALLERY = [
  { key: "", label: "Debian 12", desc: "Image locale, la plus rapide à créer", Icon: Box },
  { key: "ubuntu:24.04", label: "Ubuntu", desc: "Distribution générale", Icon: Box },
  { key: "alpine:3.19", label: "Alpine", desc: "Distribution minimale", Icon: Box },
  { key: "nginx:latest", label: "Nginx", desc: "Serveur web / reverse proxy", Icon: Globe },
  { key: "httpd:latest", label: "Apache", desc: "Serveur web", Icon: Globe },
  { key: "postgres:16", label: "PostgreSQL", desc: "Base de données relationnelle", Icon: Database },
  { key: "mysql:8", label: "MySQL", desc: "Base de données relationnelle", Icon: Database },
  { key: "mariadb:11", label: "MariaDB", desc: "Base de données relationnelle", Icon: Database },
  { key: "mongo:latest", label: "MongoDB", desc: "Base de données documents", Icon: Database },
  { key: "redis:latest", label: "Redis", desc: "Cache / clé-valeur en mémoire", Icon: Zap },
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
    fetchContainers().then(setContainers).catch((e) => pushToast({ kind: "error", title: "Erreur conteneurs", message: e.message }));
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
      pushToast({ kind: "success", title: "Conteneur créé", message: `${form.name} — construction du système en cours, patientez quelques instants` });
      setCreating(false);
      setForm(DEFAULT_FORM);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de création", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleStart(ct) {
    try { await startContainer(ct.nom); pushToast({ kind: "success", title: "Démarré", message: ct.nom }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: "Échec", message: e.message }); }
  }
  async function handleStop(ct) {
    try { await stopContainer(ct.nom); pushToast({ kind: "success", title: "Arrêt demandé", message: ct.nom }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: "Échec", message: e.message }); }
  }
  async function handleDelete() {
    if (!toDelete) return;
    try {
      await deleteContainer(toDelete.nom);
      pushToast({ kind: "success", title: "Conteneur supprimé", message: toDelete.nom });
      setToDelete(null);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    }
  }

  async function handleClone(ct) {
    const newName = window.prompt(`Nom de la copie de '${ct.nom}' :`, `${ct.nom}-clone`);
    if (!newName || !newName.trim()) return;
    try {
      await cloneContainer(ct.nom, newName.trim());
      pushToast({ kind: "success", title: "Conteneur cloné", message: `${ct.nom} → ${newName.trim()}` });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec du clonage", message: e.message });
    }
  }

  async function handleBackup(ct) {
    try {
      await createContainerBackup(ct.nom);
      pushToast({ kind: "success", title: "Sauvegarde lancée", message: ct.nom });
      setTimeout(reloadBackups, 2000);
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la sauvegarde", message: e.message });
    }
  }

  async function handleRestoreBackup(b) {
    const newName = window.prompt(`Restaurer la sauvegarde de '${b.container_name}' sous quel nom ?`, `${b.container_name}-restauré`);
    if (!newName || !newName.trim()) return;
    try {
      await restoreContainerBackup(b.id, newName.trim());
      pushToast({ kind: "success", title: "Conteneur restauré", message: newName.trim() });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la restauration", message: e.message });
    }
  }

  async function handleDeleteBackup(b) {
    if (!window.confirm(`Supprimer définitivement cette sauvegarde de '${b.container_name}' ?`)) return;
    try {
      await deleteContainerBackup(b.id);
      pushToast({ kind: "success", title: "Sauvegarde supprimée" });
      await reloadBackups();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    }
  }

  function openTerminal(ct) {
    window.open(`/container-terminal/${encodeURIComponent(ct.nom)}`, `hyperlite-ct-terminal-${ct.nom}`, "width=1000,height=700,noopener");
  }

  if (containers == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  return (
    <div className="space-y-3">
      <p className="text-xs text-anthracite-500 max-w-2xl">
        Conteneurs LXC (pilote natif libvirt, indépendant des VM QEMU/KVM) : un système Debian 12 minimal,
        accès terminal par la même clé d'automatisation SSH que les VM. La toute première création télécharge
        et prépare l'image de base (quelques minutes) ; les suivantes sont rapides (copie locale).
      </p>

      {isAdmin && (
        <button className="btn-primary" onClick={() => setCreating((c) => !c)}>
          <Plus size={14} /> Créer un conteneur
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
                placeholder="Autre image Docker Hub — cherchez ou tapez une référence, ex. traefik, ghcr.io/foo/bar:tag"
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
                          {r.officielle && <span className="shrink-0 rounded bg-accent-blue/20 px-1 text-[10px] text-accent-blue">officielle</span>}
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
              <p className="mt-1 text-[11px] text-anthracite-500">Image sélectionnée : <span className="text-anthracite-300">{form.image}</span></p>
            )}
          </div>

          <div className="grid grid-cols-4 gap-2">
            <input className="input" placeholder="Nom" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input className="input" type="number" min={1} max={16} placeholder="vCPU" value={form.vcpu} onChange={(e) => setForm((f) => ({ ...f, vcpu: Number(e.target.value) }))} />
            <input className="input" type="number" min={128} step={128} placeholder="RAM (Mo)" value={form.memory_mb} onChange={(e) => setForm((f) => ({ ...f, memory_mb: Number(e.target.value) }))} />
            <input className="input" placeholder="Réseau" value={form.network} onChange={(e) => setForm((f) => ({ ...f, network: e.target.value }))} />
            <input className="input" placeholder="Utilisateur" value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} />
            <input className="input" type="password" placeholder="Mot de passe" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn-secondary" onClick={() => setCreating(false)}>Annuler</button>
            <button className="btn-primary" disabled={busy || !form.name || !form.username || !form.password} onClick={handleCreate}>
              {busy ? "Création..." : "Créer"}
            </button>
          </div>
        </div>
      )}

      <div className="card divide-y divide-anthracite-600">
        {containers.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucun conteneur pour l'instant.</div>}
        {containers.map((ct) => (
          <div key={ct.nom} className="flex items-center gap-3 px-4 py-3 text-sm">
            <Box size={15} className="text-anthracite-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-anthracite-100">{ct.nom}</div>
              <div className="text-xs text-anthracite-400">{ct.vcpu} vCPU, {ct.memoire_mo} Mo{ct.ip ? ` — ${ct.ip}` : ""}</div>
            </div>
            <StatusBadge etat={ct.etat === "actif" ? "actif" : "arrete"} />
            {isAdmin && ct.etat === "actif" && (
              <button className="btn-secondary" title="Terminal" onClick={() => openTerminal(ct)}><TerminalSquare size={13} /></button>
            )}
            {isAdmin && ct.etat !== "actif" && (
              <button className="btn-secondary" title="Démarrer" onClick={() => handleStart(ct)}><Play size={13} /></button>
            )}
            {isAdmin && ct.etat === "actif" && (
              <button className="btn-secondary" title="Arrêter" onClick={() => handleStop(ct)}><Square size={13} /></button>
            )}
            {isAdmin && ct.etat !== "actif" && (
              <button className="btn-secondary" title="Cloner" onClick={() => handleClone(ct)}><Copy size={13} /></button>
            )}
            {isAdmin && ct.etat !== "actif" && (
              <button className="btn-secondary" title="Sauvegarder" onClick={() => handleBackup(ct)}><Archive size={13} /></button>
            )}
            {isAdmin && <button className="btn-danger" title="Supprimer" onClick={() => setToDelete(ct)}><Trash2 size={13} /></button>}
          </div>
        ))}
      </div>

      {isAdmin && backups && backups.length > 0 && (
        <div className="card">
          <div className="px-4 py-2.5 border-b border-anthracite-600">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-anthracite-100">
              <Archive size={15} /> Sauvegardes de conteneurs
            </h3>
          </div>
          <div className="divide-y divide-anthracite-600">
            {backups.map((b) => (
              <div key={b.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <div className="flex-1 min-w-0">
                  <div className="text-anthracite-100 truncate">{b.container_name}</div>
                  <div className="text-xs text-anthracite-400">
                    {new Date(b.cree_le).toLocaleString()} — {b.taille_octets ? `${(b.taille_octets / 1024 / 1024).toFixed(0)} Mo` : "..."} — {b.statut}
                  </div>
                </div>
                {b.statut === "termine" && (
                  <button className="btn-secondary" title="Restaurer" onClick={() => handleRestoreBackup(b)}><RotateCcw size={13} /></button>
                )}
                <button className="btn-danger" title="Supprimer" onClick={() => handleDeleteBackup(b)}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!toDelete}
        title="Supprimer le conteneur"
        message={`Supprimer définitivement "${toDelete?.nom}" (système de fichiers inclus) ?`}
        confirmLabel="Supprimer"
        onConfirm={handleDelete}
        onCancel={() => setToDelete(null)}
      />
    </div>
  );
}
