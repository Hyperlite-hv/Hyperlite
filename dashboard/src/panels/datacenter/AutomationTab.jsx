import { useCallback, useEffect, useState } from "react";
import { Zap, Plus, Play, Trash2, ChevronDown, ChevronUp, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { fetchJobs, createJob, deleteJob, runJob, fetchJobRuns, fetchJobRun } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { statusLabel } from "../../lib/labels";


// Real: GET/POST/DELETE /jobs, POST /jobs/{id}/run, GET /jobs/{id}/runs,
// GET /jobs/runs/{id} (see app/routers/jobs.py). A small Ansible/RMM-style
// engine: a predefined job ("Deploy a load balancer") is always present, created
// automatically when the backend starts.
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
    fetchJobs().then(setJobs).catch((e) => pushToast({ kind: "error", title: "Jobs error", message: e.message }));
  }, [pushToast]);

  useEffect(() => { reload(); }, [reload]);

  async function toggleExpand(job) {
    if (expanded === job.id) { setExpanded(null); return; }
    setExpanded(job.id);
    try {
      const r = await fetchJobRuns(job.id);
      setRuns((prev) => ({ ...prev, [job.id]: r }));
    } catch (e) { pushToast({ kind: "error", title: "History error", message: e.message }); }
  }

  async function openRunDetail(runId) {
    if (openRun === runId) { setOpenRun(null); return; }
    setOpenRun(runId);
    try { setRunDetail(await fetchJobRun(runId)); }
    catch (e) { pushToast({ kind: "error", title: "Detail error", message: e.message }); }
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
      pushToast({ kind: "success", title: "Job created", message: form.name });
      setCreating(false);
      setForm({ name: "", description: "", steps: [{ cible_type: "host", cible: "", commande: "", condition_type: "exit_code", condition_valeur: "0" }] });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(job) {
    try {
      await deleteJob(job.id);
      pushToast({ kind: "success", title: "Job deleted", message: job.name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  async function handleRun(job, dryRun) {
    const raw = runForm[job.id] || "";
    const targets = raw.split(",").map((t) => t.trim()).filter(Boolean);
    try {
      await runJob(job.id, targets, dryRun);
      pushToast({ kind: "success", title: dryRun ? "Dry run started" : "Run started", message: `${job.name}: see the history below in a few seconds` });
      setExpanded(job.id);
      setTimeout(async () => {
        try {
          const r = await fetchJobRuns(job.id);
          setRuns((prev) => ({ ...prev, [job.id]: r }));
        } catch { /* ignore */ }
      }, 3000);
    } catch (e) {
      pushToast({ kind: "error", title: "Launch failed", message: e.message });
    }
  }

  if (jobs == null) return <div className="card p-4 text-sm text-anthracite-400">Loading...</div>;

  return (
    <div className="space-y-3">
      {isAdmin && (
        <button className="btn-primary" onClick={() => setCreating((c) => !c)}>
          <Plus size={14} /> Create a custom job
        </button>
      )}

      {creating && (
        <div className="card p-4 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <input aria-label="Job name" className="input" placeholder="Job name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input aria-label="Description (optional)" className="input" placeholder="Description (optional)" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div className="space-y-2">
            {form.steps.map((s, i) => (
              <div key={i} className="flex items-center gap-2">
                <select aria-label="Step target type" className="input w-36" value={s.cible_type} onChange={(e) => updateStep(i, { cible_type: e.target.value })}>
                  <option value="host">Host</option>
                  <option value="vm">A specific VM</option>
                  <option value="chaque_cible">Each target of the run</option>
                </select>
                {s.cible_type === "vm" && (
                  <input aria-label="VM name" className="input w-32" placeholder="VM name" value={s.cible || ""} onChange={(e) => updateStep(i, { cible: e.target.value })} />
                )}
                <input aria-label="shell command" className="input flex-1" placeholder="shell command" value={s.commande} onChange={(e) => updateStep(i, { commande: e.target.value })} />
                <select aria-label="Success condition type" className="input w-32" value={s.condition_type} onChange={(e) => updateStep(i, { condition_type: e.target.value })}>
                  <option value="exit_code">Return code</option>
                  <option value="stdout_contains">Output contains</option>
                </select>
                <input aria-label="Success condition value" className="input w-24" placeholder={s.condition_type === "exit_code" ? "0" : "pattern"} value={s.condition_valeur || ""} onChange={(e) => updateStep(i, { condition_valeur: e.target.value })} />
                <button aria-label="Delete" className="btn-danger" onClick={() => removeStep(i)}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
          <div className="flex justify-between">
            <button className="btn-secondary" onClick={addStep}><Plus size={13} /> Add a step</button>
            <div className="flex gap-2">
              <button className="btn-secondary" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary" disabled={busy || !form.name} onClick={handleCreate}>Create</button>
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
                  {job.name} {job.predefined_key && <span className="ml-1 rounded-sm bg-accent-blue/20 px-1.5 py-0.5 text-[10px] text-accent-blue">predefined</span>}
                </div>
                {job.description && <div className="text-xs text-anthracite-400 truncate">{job.description}</div>}
              </div>
              <input aria-label="targets (VMs separated by commas)"
                className="input w-48 text-xs" placeholder="targets (VMs separated by commas)"
                value={runForm[job.id] || ""} onChange={(e) => setRunForm((f) => ({ ...f, [job.id]: e.target.value }))}
              />
              {isAdmin && (
                <>
                  <button className="btn-secondary" onClick={() => handleRun(job, true)} title="Dry-run">Dry run</button>
                  <button className="btn-primary" onClick={() => handleRun(job, false)}><Play size={13} /> Run</button>
                  {!job.predefined_key && <button aria-label="Delete" className="btn-danger" onClick={() => handleDelete(job)}><Trash2 size={13} /></button>}
                </>
              )}
              <button className="text-anthracite-400" onClick={() => toggleExpand(job)}>
                {expanded === job.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
            </div>

            {expanded === job.id && (
              <div className="px-8 pb-3 space-y-2">
                <div className="text-xs text-anthracite-400">Run history:</div>
                {!runs[job.id] && <div className="text-xs text-anthracite-400">Loading...</div>}
                {runs[job.id] && runs[job.id].length === 0 && <div className="text-xs text-anthracite-400">No runs.</div>}
                {runs[job.id] && runs[job.id].map((r) => (
                  <div key={r.id} className="text-xs">
                    <button className="flex items-center gap-2 text-anthracite-300 hover:text-anthracite-100" onClick={() => openRunDetail(r.id)}>
                      {r.statut === "succes" ? <CheckCircle2 size={12} className="text-status-running" />
                        : r.statut === "echec" ? <XCircle size={12} className="text-status-error" />
                        : <Loader2 size={12} className="animate-spin text-accent-blue" />}
                      {new Date(r.started_at).toLocaleString(undefined)} — {r.dry_run ? "dry run" : "real"} — {r.resultat || statusLabel(r.statut)}
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
