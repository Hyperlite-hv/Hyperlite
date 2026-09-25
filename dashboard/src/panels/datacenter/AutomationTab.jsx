import LoadingState from "../../components/LoadingState";
import { confirmAction } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { Zap, Plus, Play, Trash2, ChevronDown, ChevronUp, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { fetchJobs, createJob, deleteJob, runJob, fetchJobRuns, fetchJobRun } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { statusLabel } from "../../lib/labels";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { NativeSelect } from "@/components/ui/native-select";


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
    if (!(await confirmAction({ title: `Delete job '${job.name}'?`, message: "The job and its run history are removed.", confirmLabel: "Delete" }))) return;
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

  if (jobs == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  return (
    <div className="space-y-3">
      {isAdmin && (
        <Button onClick={() => setCreating((c) => !c)}>
          <Plus /> Create a custom job
        </Button>
      )}

      {creating && (
        <Card className="p-4 space-y-3 animate-in fade-in-0 slide-in-from-top-1 duration-150">
          <div className="grid grid-cols-2 gap-2">
            <Input aria-label="Job name" placeholder="Job name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <Input aria-label="Description (optional)" placeholder="Description (optional)" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div className="space-y-2">
            {form.steps.map((s, i) => (
              <div key={i} className="flex items-center gap-2">
                <NativeSelect aria-label="Step target type" className="w-36" value={s.cible_type} onChange={(e) => updateStep(i, { cible_type: e.target.value })}>
                  <option value="host">Host</option>
                  <option value="vm">A specific VM</option>
                  <option value="chaque_cible">Each target of the run</option>
                </NativeSelect>
                {s.cible_type === "vm" && (
                  <Input aria-label="VM name" className="w-32" placeholder="VM name" value={s.cible || ""} onChange={(e) => updateStep(i, { cible: e.target.value })} />
                )}
                <Input aria-label="shell command" className="flex-1" placeholder="shell command" value={s.commande} onChange={(e) => updateStep(i, { commande: e.target.value })} />
                <NativeSelect aria-label="Success condition type" className="w-32" value={s.condition_type} onChange={(e) => updateStep(i, { condition_type: e.target.value })}>
                  <option value="exit_code">Return code</option>
                  <option value="stdout_contains">Output contains</option>
                </NativeSelect>
                <Input aria-label="Success condition value" className="w-24" placeholder={s.condition_type === "exit_code" ? "0" : "pattern"} value={s.condition_valeur || ""} onChange={(e) => updateStep(i, { condition_valeur: e.target.value })} />
                <Button aria-label={`Remove step ${i + 1}`} size="icon" variant="outline" className="size-9 shrink-0 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => removeStep(i)}><Trash2 size={13} /></Button>
              </div>
            ))}
          </div>
          <div className="flex justify-between">
            <Button variant="secondary" onClick={addStep}><Plus /> Add a step</Button>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => setCreating(false)}>Cancel</Button>
              <Button disabled={busy || !form.name} onClick={handleCreate}>Create</Button>
            </div>
          </div>
        </Card>
      )}

      <Card className="p-0 divide-y divide-border">
        {jobs.map((job) => (
          <div key={job.id}>
            <div className="flex items-center gap-3 px-4 py-3 transition-colors duration-150 hover:bg-muted/40">
              <Zap size={15} className="text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-foreground">
                  {job.name} {job.predefined_key && <Badge variant="secondary" className="ml-1 bg-accent-blue/20 text-accent-blue">predefined</Badge>}
                </div>
                {job.description && <div className="text-xs text-muted-foreground truncate">{job.description}</div>}
              </div>
              <Input aria-label={`Targets for ${job.name} (VMs separated by commas)`}
                className="w-48 text-xs" placeholder="targets (VMs separated by commas)"
                value={runForm[job.id] || ""} onChange={(e) => setRunForm((f) => ({ ...f, [job.id]: e.target.value }))}
              />
              {isAdmin && (
                <>
                  <Button aria-label={`Dry run ${job.name}`} variant="secondary" size="sm" onClick={() => handleRun(job, true)} title="Dry-run">Dry run</Button>
                  <Button aria-label={`Run ${job.name}`} size="sm" onClick={() => handleRun(job, false)}><Play /> Run</Button>
                  {!job.predefined_key && <Button aria-label={`Delete job ${job.name}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => handleDelete(job)}><Trash2 size={13} /></Button>}
                </>
              )}
              <Button variant="ghost" size="icon" className="size-7 text-muted-foreground" aria-label={`Show run history of ${job.name}`} onClick={() => toggleExpand(job)}>
                {expanded === job.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </Button>
            </div>

            {expanded === job.id && (
              <div className="px-8 pb-3 space-y-2 animate-in fade-in-0 duration-150">
                <div className="text-xs text-muted-foreground">Run history:</div>
                {!runs[job.id] && <div className="text-xs text-muted-foreground"><LoadingState /></div>}
                {runs[job.id] && runs[job.id].length === 0 && <div className="text-xs text-muted-foreground">No runs.</div>}
                {runs[job.id] && runs[job.id].map((r) => (
                  <div key={r.id} className="text-xs">
                    <button className="flex items-center gap-2 text-foreground/80 transition-colors duration-150 hover:text-foreground" onClick={() => openRunDetail(r.id)}>
                      {r.statut === "succes" ? <CheckCircle2 size={12} className="text-status-running" />
                        : r.statut === "echec" ? <XCircle size={12} className="text-status-error" />
                        : <Loader2 size={12} className="animate-spin text-accent-blue" />}
                      {new Date(r.started_at).toLocaleString(undefined)} — {r.dry_run ? "dry run" : "real"} — {r.resultat || statusLabel(r.statut)}
                    </button>
                    {openRun === r.id && runDetail && (
                      <div className="mt-1 ml-5 space-y-1 rounded-md bg-muted/60 p-2 font-mono">
                        {runDetail.logs.map((l) => (
                          <div key={l.id} className={l.reussi ? "text-foreground/80" : "text-status-error"}>
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
      </Card>
    </div>
  );
}
