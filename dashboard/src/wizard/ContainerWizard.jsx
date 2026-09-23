import { useEffect, useState } from "react";
import { Check, Star } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";
import { createContainer, searchDockerHub } from "../api/client";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// Deliberately a single screen (no steps like VMWizard): a container is created
// with far fewer choices than a VM (no ISO/OS to pick). See
// app/core/container_builder.py. Reachable directly from the "Create container"
// button of the Header, next to "Create VM".
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

  // No `if (!open) return null` here: see the note in VMWizard.jsx — it would
  // unmount the <Dialog> itself and skip Radix's close animation / focus-restore.

  function patch(fields) {
    setForm((f) => ({ ...f, ...fields }));
  }
  function reset() {
    setForm(initialForm(networks));
  }
  function closeAndReset() {
    onClose();
    reset();
  }

  async function handleCreate() {
    setBusy(true);
    const taskId = addTask({ type: "create_container", cible: form.name });
    try {
      await createContainer(form);
      completeTask(taskId, "termine");
      pushToast({ kind: "success", title: "Container created", message: `${form.name}: building the system` });
      onClose();
      reset();
    } catch (e) {
      completeTask(taskId, "echec", e.message);
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  const canCreate = form.name && form.username && form.password.length >= 4 && !busy;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) closeAndReset(); }}>
      <DialogContent className="w-full max-w-md p-0 gap-0 overflow-hidden">
        <DialogHeader className="border-b border-border px-5 py-3 space-y-0">
          <DialogTitle>Create a container</DialogTitle>
        </DialogHeader>

        <div className="space-y-3 px-5 py-4">
          <p className="text-xs text-muted-foreground">
            LXC container, terminal access through the automation SSH key. The very first creation of a given image prepares its base (a few minutes); the following ones are fast.
          </p>

          <div>
            <Label className="text-xs font-medium text-foreground/80">Name</Label>
            <Input aria-label="Name" className="mt-1 w-full" value={form.name} onChange={(e) => patch({ name: e.target.value })} autoFocus />
          </div>

          <div>
            <Label className="text-xs font-medium text-foreground/80">Image source</Label>
            <div className="mt-1 flex gap-2">
              <Button
                type="button"
                variant={form.image === "" ? "default" : "secondary"}
                size="sm"
                className="flex-1"
                onClick={() => patch({ image: "" })}
              >
                Debian 12 (local base)
              </Button>
              <Button
                type="button"
                variant={form.image !== "" ? "default" : "secondary"}
                size="sm"
                className="flex-1"
                onClick={() => patch({ image: form.image || "alpine:3.19" })}
              >
                Docker Hub image
              </Button>
            </div>
            {form.image !== "" && (
              <div className="mt-2 space-y-2 animate-in fade-in-0 duration-150">
                <Input aria-label="Search Docker Hub (e.g. apache, nginx, postgres...)"
                  className="w-full"
                  placeholder="Search Docker Hub (e.g. apache, nginx, postgres...)"
                  value={dockerQuery}
                  onChange={(e) => setDockerQuery(e.target.value)}
                />
                {searching && <p className="text-xs text-muted-foreground">Searching...</p>}
                {dockerResults.length > 0 && (
                  <div className="max-h-44 overflow-y-auto rounded-md border border-border divide-y divide-border">
                    {dockerResults.map((r) => (
                      <button
                        type="button"
                        key={r.nom}
                        onClick={() => { patch({ image: `${r.nom}:latest` }); setDockerQuery(""); setDockerResults([]); }}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors duration-150 hover:bg-muted"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5 text-sm text-foreground">
                            <span className="truncate">{r.nom}</span>
                            {r.officielle && <Badge variant="secondary" className="shrink-0 bg-accent-blue/20 text-accent-blue">official</Badge>}
                          </div>
                          {r.description && <div className="truncate text-xs text-muted-foreground">{r.description}</div>}
                        </div>
                        <div className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                          <Star size={11} /> {r.etoiles}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
                <div>
                  <Label className="text-xs font-medium text-foreground/80">Selected image</Label>
                  <Input aria-label="Selected image"
                    className="mt-1 w-full"
                    placeholder="e.g. ubuntu:22.04, alpine:3.19, debian:12"
                    value={form.image}
                    onChange={(e) => patch({ image: e.target.value })}
                  />
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Search then pick an image, or type a Docker Hub reference (or any OCI registry) directly: the image is pulled and then given SSH/sudo automatically.
                </p>
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs font-medium text-foreground/80">vCPU</Label>
              <Input aria-label="vCPU" className="mt-1 w-full" type="number" min={1} max={16} value={form.vcpu} onChange={(e) => patch({ vcpu: Number(e.target.value) })} />
            </div>
            <div>
              <Label className="text-xs font-medium text-foreground/80">RAM (MB)</Label>
              <Input aria-label="RAM (MB)" className="mt-1 w-full" type="number" min={128} step={128} value={form.memory_mb} onChange={(e) => patch({ memory_mb: Number(e.target.value) })} />
            </div>
          </div>

          <div>
            <Label className="text-xs font-medium text-foreground/80">Network</Label>
            <Select value={form.network} onValueChange={(v) => patch({ network: v })}>
              <SelectTrigger aria-label="Network" className="mt-1 w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {networks.length === 0 && <SelectItem value="default">default</SelectItem>}
                {networks.map((n) => <SelectItem key={n.nom} value={n.nom}>{n.nom}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs font-medium text-foreground/80">User</Label>
              <Input aria-label="User" className="mt-1 w-full" value={form.username} onChange={(e) => patch({ username: e.target.value })} />
            </div>
            <div>
              <Label className="text-xs font-medium text-foreground/80">Password</Label>
              <Input aria-label="Password" className="mt-1 w-full" type="password" value={form.password} onChange={(e) => patch({ password: e.target.value })} />
            </div>
          </div>
        </div>

        <DialogFooter className="border-t border-border px-5 py-3">
          <Button variant="secondary" onClick={closeAndReset}>Cancel</Button>
          <Button disabled={!canCreate} onClick={handleCreate}>
            <Check /> {busy ? "Creating..." : "Create the container"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
