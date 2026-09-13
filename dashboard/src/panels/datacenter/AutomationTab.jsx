import { useCallback, useEffect, useState } from "react";
import { Zap, Plus, Play, Trash2, ChevronDown, ChevronUp, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { fetchJobs, createJob, deleteJob, runJob, fetchJobRuns, fetchJobRun } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";

const CIBLE_LABELS = { vm: "VM précise", host: "Hôte", chaque_cible: "Chaque cible du run" };

// Reel : GET/POST/DELETE /jobs, POST /jobs/{id}/run, GET /jobs/{id}/runs,
// GET /jobs/runs/{id} (voir app/routers/jobs.py, chantier 14). Mini moteur
// façon Ansible/RMM -- un job prédéfini ("Déployer un load balancing") est
// toujours présent, créé automatiquement au démarrage du backend.
export default function AutomationTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [jobs, setJobs] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [runs, setRuns] = useState({});
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", steps: [{ cible_type: "host", cible: "", commande: "", condition_type: "exit_code", condition_valeur: "0" }] });
  const [runForm, setRunForm] = useState({});
  const [busy, setBusy] = useState(false);
  const [openRun, setOpenRun] = useState(null);
  const [runDetail, setRunDetail] = useState(null);

  const reload = useCallback(() => {
    fetchJobs().then(setJobs).catch((e) => pushToast({ kind: "error", title: "Erreur jobs", message: e.message }));
  }, [pushToast]);

  useEffect(() => { reload(); }, [reload]);

  async function toggleExpand(job) {
    if (expanded === job.id) { setExpanded(null); return; }
    setExpanded(job.id);
    try {
      const r = await fetchJobRuns(job.id);
      setRuns((prev) => ({ ...prev, [job.id]: r }));
    } catch (e) { pushToast({ kind: "error", title: "Erreur historique", message: e.message }); }
  }

  async function openRunDetail(runId) {
    if (openRun === runId) { setOpenRun(null); return; }
    setOpenRun(runId);
    try { setRunDetail(await fetchJobRun(runId)); }
    catch (e) { pushToast({ kind: "error", title: "Erreur détail", message: e.message }); }
  }

  function updateStep(i, patch) {
    setForm((f) => ({ ...f, steps: f.steps.map((s, idx) => (idx === i ? { ...s, ...patch } : s)) }));
  }
  function addStep() {
    setForm((f) => ({ ...f, steps: [...f.steps, { cible_type: "host", cible: "", commande: "", condition_type: "exit_code", condition_valeur: "0" }] }));
  }
  function removeStep(i) {
    setForm((f) => ({ ...f, steps: f.steps.filter((_, idx) => idx !== i) }));
  }

  async function handleCreate() {
    setBusy(true);
    try {
      await createJob(form);
      pushToast({ kind: "success", title: "Job créé", message: form.name });
      setCreating(false);
      setForm({ name: "", description: "", steps: [{ cible_type: "host", cible: "", commande: "", condition_type: "exit_code", condition_valeur: "0" }] });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la création", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(job) {
    try {
      await deleteJob(job.id);
      pushToast({ kind: "success", title: "Job supprimé", message: job.name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    }
  }

  async function handleRun(job, dryRun) {
    const raw = runForm[job.id] || "";
    const targets = raw.split(",").map((t) => t.trim()).filter(Boolean);
    try {
      await runJob(job.id, targets, dryRun);
      pushToast({ kind: "success", title: dryRun ? "Simulation lancée" : "Exécution lancée", message: `${job.name} — voir l'historique ci-dessous dans quelques secondes` });
      setExpanded(job.id);
      setTimeout(async () => {
        try {
          const r = await fetchJobRuns(job.id);
          setRuns((prev) => ({ ...prev, [job.id]: r }));
        } catch (e) { /* ignore */ }
      }, 3000);
    } catch (e) {
      pushToast({ kind: "error", title: "Échec du lancement", message: e.message });
    }
  }

  if (jobs == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  return (
    <div className="space-y-3">
      {isAdmin && (
        <button className="btn-primary" onClick={() => setCreating((c) => !c)}>
          <Plus size={14} /> Créer un job personnalisé
        </button>
      )}

      {creating && (
        <div className="card p-4 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <input className="input" placeholder="Nom du job" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input className="input" placeholder="Description (optionnel)" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div className="space-y-2">
            {form.steps.map((s, i) => (
              <div key={i} className="flex items-center gap-2">
                <select className="input w-36" value={s.cible_type} onChange={(e) => updateStep(i, { cible_type: e.target.value })}>
                  <option value="host">Hôte</option>
                  <option value="vm">VM précise</option>
                  <option value="chaque_cible">Chaque cible du run</option>
                </select>
                {s.cible_type === "vm" && (
                  <input className="input w-32" placeholder="nom VM" value={s.cible || ""} onChange={(e) => updateStep(i, { cible: e.target.value })} />
                )}
                <input className="input flex-1" placeholder="commande shell" value={s.commande} onChange={(e) => updateStep(i, { commande: e.target.value })} />
                <select className="input w-32" value={s.condition_type} onChange={(e) => updateStep(i, { condition_type: e.target.value })}>
                  <option value="exit_code">Code retour</option>
                  <option value="stdout_contains">Sortie contient</option>
                </select>
                <input className="input w-24" placeholder={s.condition_type === "exit_code" ? "0" : "motif"} value={s.condition_valeur || ""} onChange={(e) => updateStep(i, { condition_valeur: e.target.value })} />
                <button className="btn-danger" onClick={() => removeStep(i)}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
          <div className="flex justify-between">
            <button className="btn-secondary" onClick={addStep}><Plus size={13} /> Ajouter une étape</button>
            <div className="flex gap-2">
              <button className="btn-secondary" onClick={() => setCreating(false)}>Annuler</button>
              <button className="btn-primary" disabled={busy || !form.name} onClick={handleCreate}>Créer</button>
            </div>
          </div>
        </div>
      )}

      <div className="card divide-y divide-anthracite-600">
        {jobs.map((job) => (
          <div key={job.id}>
            <div className="flex items-center gap-3 px-4 py-3">
              <Zap size={15} className="text-anthracite-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-anthracite-100">
                  {job.name} {job.predefined_key && <span className="ml-1 rounded bg-accent-blue/20 px-1.5 py-0.5 text-[10px] text-accent-blue">prédéfini</span>}
                </div>
                {job.description && <div className="text-xs text-anthracite-400 truncate">{job.description}</div>}
              </div>
              <input
                className="input w-48 text-xs" placeholder="cibles (VM séparées par virgule)"
                value={runForm[job.id] || ""} onChange={(e) => setRunForm((f) => ({ ...f, [job.id]: e.target.value }))}
              />
              {isAdmin && (
                <>
                  <button className="btn-secondary" onClick={() => handleRun(job, true)} title="Dry-run">Simuler</button>
                  <button className="btn-primary" onClick={() => handleRun(job, false)}><Play size={13} /> Exécuter</button>
                  {!job.predefined_key && <button className="btn-danger" onClick={() => handleDelete(job)}><Trash2 size={13} /></button>}
                </>
              )}
              <button className="text-anthracite-400" onClick={() => toggleExpand(job)}>
                {expanded === job.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
            </div>

            {expanded === job.id && (
              <div className="px-8 pb-3 space-y-2">
                <div className="text-xs text-anthracite-500">Historique des exécutions :</div>
                {!runs[job.id] && <div className="text-xs text-anthracite-400">Chargement...</div>}
                {runs[job.id] && runs[job.id].length === 0 && <div className="text-xs text-anthracite-400">Aucune exécution.</div>}
                {runs[job.id] && runs[job.id].map((r) => (
                  <div key={r.id} className="text-xs">
                    <button className="flex items-center gap-2 text-anthracite-300 hover:text-anthracite-100" onClick={() => openRunDetail(r.id)}>
                      {r.statut === "succes" ? <CheckCircle2 size={12} className="text-status-running" />
                        : r.statut === "echec" ? <XCircle size={12} className="text-status-error" />
                        : <Loader2 size={12} className="animate-spin text-accent-blue" />}
                      {new Date(r.started_at).toLocaleString("fr-FR")} — {r.dry_run ? "simulation" : "réel"} — {r.resultat || r.statut}
                    </button>
                    {openRun === r.id && runDetail && (
                      <div className="mt-1 ml-5 space-y-1 rounded-md bg-anthracite-700/60 p-2 font-mono">
                        {runDetail.logs.map((l) => (
                          <div key={l.id} className={l.reussi ? "text-anthracite-300" : "text-status-error"}>
                            [{l.cible}] {l.commande} → exit={l.exit_code} {l.stdout ? `| ${l.stdout.trim().slice(0, 200)}` : ""}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
