import { useEffect, useState } from "react";
import { X, Check, Star } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";
import { createContainer, searchDockerHub } from "../api/client";

// Volontairement un seul ecran (pas d'etapes comme VMWizard) : un conteneur
// se cree avec beaucoup moins de choix qu'une VM (pas d'ISO/OS a choisir,
// une seule image de base pour l'instant -- voir app/core/
// container_builder.py, chantier 18). Accessible directement depuis le
// bouton "Créer conteneur" du Header, a cote de "Créer VM".
function initialForm(networks) {
  return { name: "", vcpu: 1, memory_mb: 512, username: "", password: "", network: networks[0]?.nom || "default", image: "" };
}

export default function ContainerWizard({ open, onClose }) {
  const networks = useInfraStore((s) => s.networks);
  const addTask = useInfraStore((s) => s.addTask);
  const completeTask = useInfraStore((s) => s.completeTask);
  const pushToast = useInfraStore((s) => s.pushToast);

  const [form, setForm] = useState(() => initialForm(networks));
  const [busy, setBusy] = useState(false);
  const [dockerQuery, setDockerQuery] = useState("");
  const [dockerResults, setDockerResults] = useState([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (form.image === "" || !dockerQuery.trim()) { setDockerResults([]); return; }
    setSearching(true);
    const id = setTimeout(() => {
      searchDockerHub(dockerQuery).then(setDockerResults).catch(() => setDockerResults([])).finally(() => setSearching(false));
    }, 400);
    return () => clearTimeout(id);
  }, [dockerQuery, form.image]);

  if (!open) return null;

  function patch(fields) {
    setForm((f) => ({ ...f, ...fields }));
  }
  function reset() {
    setForm(initialForm(networks));
  }

  async function handleCreate() {
    setBusy(true);
    const taskId = addTask({ type: "create_container", cible: form.name });
    try {
      await createContainer(form);
      completeTask(taskId, "termine");
      pushToast({ kind: "success", title: "Conteneur créé", message: `${form.name} — construction du système en cours` });
      onClose();
      reset();
    } catch (e) {
      completeTask(taskId, "echec", e.message);
      pushToast({ kind: "error", title: "Échec de création", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  const canCreate = form.name && form.username && form.password.length >= 4 && !busy;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md overflow-hidden">
        <div className="flex items-center justify-between border-b border-anthracite-600 px-5 py-3">
          <h2 className="text-sm font-semibold text-anthracite-100">Créer un conteneur</h2>
          <button onClick={() => { onClose(); reset(); }} className="text-anthracite-400 hover:text-anthracite-100"><X size={16} /></button>
        </div>

        <div className="space-y-3 px-5 py-4">
          <p className="text-xs text-anthracite-500">
            Conteneur LXC, accès terminal par clé SSH d'automatisation.
            La toute première création d'une image donnée prépare sa base (quelques minutes) ; les suivantes sont rapides.
          </p>

          <div>
            <label className="text-xs font-medium text-anthracite-300">Nom</label>
            <input className="input mt-1 w-full" value={form.name} onChange={(e) => patch({ name: e.target.value })} autoFocus />
          </div>

          <div>
            <label className="text-xs font-medium text-anthracite-300">Source de l'image</label>
            <div className="mt-1 flex gap-2">
              <button
                type="button"
                className={form.image === "" ? "btn-primary flex-1 !py-1.5 text-xs" : "btn-secondary flex-1 !py-1.5 text-xs"}
                onClick={() => patch({ image: "" })}
              >
                Debian 12 (base locale)
              </button>
              <button
                type="button"
                className={form.image !== "" ? "btn-primary flex-1 !py-1.5 text-xs" : "btn-secondary flex-1 !py-1.5 text-xs"}
                onClick={() => patch({ image: form.image || "alpine:3.19" })}
              >
                Image Docker Hub
              </button>
            </div>
            {form.image !== "" && (
              <div className="mt-2 space-y-2">
                <input
                  className="input w-full"
                  placeholder="Rechercher sur Docker Hub (ex. apache, nginx, postgres...)"
                  value={dockerQuery}
                  onChange={(e) => setDockerQuery(e.target.value)}
                />
                {searching && <p className="text-xs text-anthracite-500">Recherche...</p>}
                {dockerResults.length > 0 && (
                  <div className="max-h-44 overflow-y-auto rounded-md border border-anthracite-600 divide-y divide-anthracite-600">
                    {dockerResults.map((r) => (
                      <button
                        type="button"
                        key={r.nom}
                        onClick={() => { patch({ image: `${r.nom}:latest` }); setDockerQuery(""); setDockerResults([]); }}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-anthracite-700"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5 text-sm text-anthracite-100">
                            <span className="truncate">{r.nom}</span>
                            {r.officielle && <span className="shrink-0 rounded bg-accent-blue/20 px-1 text-[10px] text-accent-blue">officielle</span>}
                          </div>
                          {r.description && <div className="truncate text-xs text-anthracite-400">{r.description}</div>}
                        </div>
                        <div className="flex shrink-0 items-center gap-1 text-xs text-anthracite-500">
                          <Star size={11} /> {r.etoiles}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
                <div>
                  <label className="text-xs font-medium text-anthracite-300">Image sélectionnée</label>
                  <input
                    className="input mt-1 w-full"
                    placeholder="ex. ubuntu:22.04, alpine:3.19, debian:12"
                    value={form.image}
                    onChange={(e) => patch({ image: e.target.value })}
                  />
                </div>
                <p className="text-[11px] text-anthracite-500">
                  Cherchez puis choisissez une image, ou saisissez directement une référence Docker Hub (ou tout
                  registre OCI) — l'image est tirée puis dotée de SSH/sudo automatiquement.
                </p>
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-medium text-anthracite-300">vCPU</label>
              <input className="input mt-1 w-full" type="number" min={1} max={16} value={form.vcpu} onChange={(e) => patch({ vcpu: Number(e.target.value) })} />
            </div>
            <div>
              <label className="text-xs font-medium text-anthracite-300">RAM (Mo)</label>
              <input className="input mt-1 w-full" type="number" min={128} step={128} value={form.memory_mb} onChange={(e) => patch({ memory_mb: Number(e.target.value) })} />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium text-anthracite-300">Réseau</label>
            <select className="input mt-1 w-full" value={form.network} onChange={(e) => patch({ network: e.target.value })}>
              {networks.length === 0 && <option value="default">default</option>}
              {networks.map((n) => <option key={n.nom} value={n.nom}>{n.nom}</option>)}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-medium text-anthracite-300">Utilisateur</label>
              <input className="input mt-1 w-full" value={form.username} onChange={(e) => patch({ username: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-anthracite-300">Mot de passe</label>
              <input className="input mt-1 w-full" type="password" value={form.password} onChange={(e) => patch({ password: e.target.value })} />
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-anthracite-600 px-5 py-3">
          <button className="btn-secondary" onClick={() => { onClose(); reset(); }}>Annuler</button>
          <button className="btn-primary" disabled={!canCreate} onClick={handleCreate}>
            <Check size={14} /> {busy ? "Création..." : "Créer le conteneur"}
          </button>
        </div>
      </div>
    </div>
  );
}
