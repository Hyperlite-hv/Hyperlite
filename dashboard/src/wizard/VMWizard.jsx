import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Check } from "lucide-react";
import StepNode from "./steps/StepNode";
import StepTemplate from "./steps/StepTemplate";
import StepResources from "./steps/StepResources";
import StepNetwork from "./steps/StepNetwork";
import StepReview from "./steps/StepReview";
import { useInfraStore } from "../store/useInfraStore";
import { confirmAction } from "../store/useConfirmStore";
import { createVM, fetchHostProfile } from "../api/client";
import { installationFamily, isWindowsInstall } from "../utils/osFamily";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

const STEPS = [
  { id: "node", label: "Node", Component: StepNode },
  { id: "template", label: "Template", Component: StepTemplate },
  { id: "resources", label: "CPU / RAM / Disk", Component: StepResources },
  { id: "network", label: "Network", Component: StepNetwork },
  { id: "review", label: "Summary", Component: StepReview },
];

function initialForm(nodes, networks, defaults) {
  return {
    node: nodes[0]?.id || "",
    iso: "",
    driversIso: "",
    guestOs: "auto",
    diskController: "auto",
    importDisk: null,
    name: "",
    // Defaults come from the deployment PROFILE (GET /host/profile), already bounded
    // by the real host limits; falls back to the historical values if the call fails.
    vcpu: defaults?.vcpu ?? 1,
    memory_mb: defaults?.memory_mb ?? 1024,
    disks: [{ size_gb: defaults?.disk_gb ?? 10 }],
    username: "",
    password: "",
    network: networks[0]?.nom || "default",
    // Storage pool choice: empty = the 'default' pool (historical behaviour,
    // unchanged).
    storagePool: "",
    autoCleanupEnabled: false,
    autoCleanupDays: 7,
  };
}

export default function VMWizard({ open, onClose, triggerRef }) {
  const nodes = useInfraStore((s) => s.nodes);
  const networks = useInfraStore((s) => s.networks);
  const storagePools = useInfraStore((s) => s.storagePools);
  const addTask = useInfraStore((s) => s.addTask);
  const completeTask = useInfraStore((s) => s.completeTask);
  const loadAll = useInfraStore((s) => s.loadAll);

  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState(() => initialForm(nodes, networks));
  const [profileDefaults, setProfileDefaults] = useState(null);
  // Creation is not idempotent: `busy` blocks a second submit, `error` keeps the dialog (and the typed
  // values) open with a readable message when the backend refuses.
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    fetchHostProfile().then((p) => {
      if (!alive) return;
      const d = p.vm_defaults_effectifs;
      setProfileDefaults(d);
      // Only overwrites resources that are still untouched (historical values).
      setForm((f) => (f.vcpu === 1 && f.memory_mb === 1024 && f.disks.length === 1 && f.disks[0].size_gb === 10
        ? { ...f, vcpu: d.vcpu, memory_mb: d.memory_mb, disks: [{ size_gb: d.disk_gb }] } : f));
    }).catch(() => {});
    return () => { alive = false; };
  }, [open]);

  // No `if (!open) return null` here: that would unmount the <Dialog> element
  // itself the instant it closes, which skips Radix's own close animation AND
  // its focus-restore-to-trigger behavior (both rely on the Dialog staying
  // mounted while its internal Presence handles hiding the content). `open`
  // is passed straight through to Radix, which already renders nothing while
  // closed.

  function patch(fields) {
    setForm((f) => {
      const next = { ...f, ...fields };
      if (isWindowsInstall(next) && !isWindowsInstall(f)) {
        return { ...next, driversIso: "", vcpu: Math.max(next.vcpu, 2), memory_mb: Math.max(next.memory_mb, 4096),
          disks: next.disks.map((d, i) => i === 0 ? { ...d, size_gb: Math.max(d.size_gb, 64) } : d) };
      }
      return next;
    });
  }

  function reset() {
    setStepIndex(0);
    setForm(initialForm(nodes, networks, profileDefaults));
  }

  function closeAndReset() {
    onClose();
    reset();
    setError(null);
  }

  // Closing with typed values asks first, so a stray Escape or click never loses the draft.
  const dirty = Boolean(form.name || form.username || form.password || form.iso || (form.importDisk && form.importDisk !== "__pending__"));
  async function requestClose() {
    if (busy) return;
    if (dirty && !(await confirmAction({ title: "Discard this draft?", message: "The values you entered will be lost.", confirmLabel: "Discard" }))) return;
    closeAndReset();
  }

  async function handleCreate() {
    if (busy) return;
    setBusy(true);
    setError(null);
    // Same payload shape as POST /vms on the real backend (app/routers/vms.py
    // VMCreate): name, vcpu, memory_mb, disks[], network, username, password, iso.
    const payload = {
      name: form.name, vcpu: form.vcpu, memory_mb: form.memory_mb,
      disks: form.disks, network: form.network, username: form.username,
      password: form.password, iso: form.iso || null,
      drivers_iso: form.iso && !form.importDisk ? form.driversIso || null : null,
      guest_os: form.guestOs,
      disk_controller: form.diskController,
      import_disk: form.importDisk && form.importDisk !== "__pending__" ? form.importDisk : null,
      storage_pool: form.storagePool || null,
      auto_cleanup_days: form.autoCleanupEnabled ? form.autoCleanupDays : null,
    };
    const taskId = addTask({ type: "create_vm", cible: form.name, node: form.node });
    try {
      await createVM(payload);
      completeTask(taskId, "termine");
      await loadAll(); // reload from the backend rather than guessing the created state
      onClose();
      reset();
    } catch (e) {
      completeTask(taskId, "echec", e.message);
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const Step = STEPS[stepIndex].Component;
  const isLast = stepIndex === STEPS.length - 1;
  // "resources" step (index 2): the name is always required; user/password are
  // required except for a manual installation (unrecognized ISO, see
  // StepTemplate/StepResources/detectOsFamily). Without an ISO (cloud-init) or with
  // a recognized ISO (unattended installation), the account is indeed created by
  // Hyperlite, so it is always required here.
  const manualInstall = Boolean(form.iso) && !installationFamily(form);
  const importMode = form.importDisk != null;
  const canNext = stepIndex !== 2 || (form.name && (importMode ? Boolean(form.importDisk && form.importDisk !== "__pending__") : (manualInstall || (form.username && form.password.length >= 4))));

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) requestClose(); }}>
      <DialogContent
        className="w-full max-w-2xl sm:max-w-2xl p-0 gap-0 overflow-hidden"
        // Explicit instead of relying on Radix's implicit "last focused
        // element before mount" capture: the trigger button lives outside
        // this always-mounted Dialog, opened via a state toggle rather than
        // a direct user click on Radix's own DialogTrigger, which is a path
        // Radix's own auto-capture doesn't reliably cover.
        onCloseAutoFocus={(e) => {
          if (triggerRef?.current) {
            e.preventDefault();
            triggerRef.current.focus();
          }
        }}
      >
        <DialogHeader className="border-b border-border px-5 py-3 space-y-0">
          <DialogTitle>Create a virtual machine</DialogTitle>
        </DialogHeader>

        <ol className="flex items-center gap-2 px-5 py-3" aria-label="Steps">
          {STEPS.map((st, i) => (
            <li key={st.id} className="flex flex-1 items-center gap-2" aria-current={i === stepIndex ? "step" : undefined}>
              <span className={`flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${i < stepIndex ? "bg-accent-blue text-white" : i === stepIndex ? "border-2 border-accent-blue text-foreground" : "border border-border text-muted-foreground"}`}>
                {i < stepIndex ? <Check size={12} /> : i + 1}
              </span>
              <span className={`truncate text-xs ${i === stepIndex ? "font-medium text-foreground" : "hidden text-muted-foreground sm:inline"}`}>{st.label}</span>
              {i < STEPS.length - 1 && <span className={`h-px flex-1 ${i < stepIndex ? "bg-accent-blue" : "bg-border"}`} />}
            </li>
          ))}
        </ol>

        <div className="max-h-[62vh] overflow-y-auto px-5 py-2">
          <Step form={form} patch={patch} nodes={nodes} networks={networks} storagePools={storagePools} />
        </div>

        {error && (
          <div role="alert" className="mx-5 mb-2 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error">
            <strong className="font-semibold">The VM could not be created.</strong> <span className="font-mono break-words">{error}</span>
          </div>
        )}
        <DialogFooter className="border-t border-border px-5 py-3 sm:justify-between">
          <Button variant="secondary" disabled={stepIndex === 0 || busy} onClick={() => setStepIndex((i) => i - 1)}>
            <ChevronLeft /> Previous
          </Button>
          {isLast ? (
            <Button onClick={handleCreate} disabled={busy}>
              <Check /> {busy ? "Creating..." : "Create the VM"}
            </Button>
          ) : (
            <Button disabled={!canNext} onClick={() => setStepIndex((i) => i + 1)}>
              Next <ChevronRight />
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
