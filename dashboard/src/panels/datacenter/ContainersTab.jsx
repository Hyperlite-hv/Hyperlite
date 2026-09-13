import { useCallback, useEffect, useState } from "react";
import { Box, Plus, Trash2, Play, Square, TerminalSquare, Star } from "lucide-react";
import {
  fetchContainers, createContainer, startContainer, stopContainer, deleteContainer, searchDockerHub,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import StatusBadge from "../../components/StatusBadge";
import ConfirmDialog from "../../components/ConfirmDialog";

// Reel : GET/POST/DELETE /containers (voir app/routers/containers.py,
// chantier 18) -- conteneurs LXC via le pilote LXC natif de libvirt, en
// plus des VM QEMU/KVM existantes. Premiere version : pas de galerie de
// templates/images (une seule base Debian 12 debootstrappee au premier
// conteneur cree, mise en cache cote serveur), pas de snapshots/clonage
// pour l'instant -- a etendre plus tard si besoin, comme les autres
// chantiers de cette liste ont ete livres par etapes.
const DEFAULT_FORM = { name: "", vcpu: 1, memory_mb: 512, username: "", password: "", network: "default", image: "" };

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

  useEffect(() => { reload(); }, [reload]);
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
        <div className="card p-4 space-y-3">
          <div className="grid grid-cols-4 gap-2">
            <input className="input" placeholder="Nom" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input className="input" type="number" min={1} max={16} placeholder="vCPU" value={form.vcpu} onChange={(e) => setForm((f) => ({ ...f, vcpu: Number(e.target.value) }))} />
            <input className="input" type="number" min={128} step={128} placeholder="RAM (Mo)" value={form.memory_mb} onChange={(e) => setForm((f) => ({ ...f, memory_mb: Number(e.target.value) }))} />
            <input className="input" placeholder="Réseau" value={form.network} onChange={(e) => setForm((f) => ({ ...f, network: e.target.value }))} />
            <input className="input" placeholder="Utilisateur" value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} />
            <input className="input" type="password" placeholder="Mot de passe" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
            <div className="col-span-2 relative">
              <input
                className="input w-full"
                placeholder="Image Docker Hub (vide = base locale Debian 12) — cherchez ou tapez une référence, ex. apache, ubuntu:22.04"
                value={form.image}
                onChange={(e) => { setForm((f) => ({ ...f, image: e.target.value })); setDockerQuery(e.target.value); }}
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
            {isAdmin && <button className="btn-danger" title="Supprimer" onClick={() => setToDelete(ct)}><Trash2 size={13} /></button>}
          </div>
        ))}
      </div>

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
