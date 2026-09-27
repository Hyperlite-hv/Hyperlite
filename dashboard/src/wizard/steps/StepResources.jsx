import { Plus, X } from "lucide-react";
import { diskController, guestProfile, installationFamily } from "../../utils/osFamily";
import { useHostLimits } from "../../hooks/useHostLimits";
import OverallocationNote from "../../components/OverallocationNote";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { NativeSelect } from "@/components/ui/native-select";

// Bounds DERIVED from the real host through GET /host/limits, instead of a fixed
// 2 vCPU/2 GB cap. The user account is required without an ISO (cloud-init) AND
// with a recognized ISO (unattended installation, same logic as detect_os_family
// on the backend): only a manual installation (unrecognized ISO) does without it.
export default function StepResources({ form, patch, storagePools = [] }) {
  const limits = useHostLimits();
  // Storage pool choice: dir/netfs (classic qcow2 file path) or zfs (raw zvols, see
  // app/routers/vms.py::create_vm), and only ACTIVE pools, since an inactive pool
  // would make the VM creation fail.
  const selectablePools = storagePools.filter((p) => ["dir", "netfs", "zfs"].includes(p.type) && p.etat === "actif");
  const manualInstall = Boolean(form.iso) && !installationFamily(form);
  const importMode = form.importDisk != null;
  function updateDisk(i, size_gb) {
    const disks = form.disks.map((d, idx) => (idx === i ? { size_gb } : d));
    patch({ disks });
  }
  function addDisk() {
    if (limits && form.disks.length >= limits.disques.max) return;
    patch({ disks: [...form.disks, { size_gb: 5 }] });
  }
  function removeDisk(i) {
    if (form.disks.length <= 1) return;
    patch({ disks: form.disks.filter((_, idx) => idx !== i) });
  }

  return (
    <div className="space-y-4">
      <div>
        <Label className="text-xs font-medium text-foreground/80">VM name</Label>
        <Input aria-label="VM name" className="mt-1" value={form.name} onChange={(e) => patch({ name: e.target.value })} placeholder="e.g. web-03" />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label className="text-xs font-medium text-foreground/80">vCPU{limits ? ` (${limits.vcpu.min}-${limits.vcpu.max})` : ""}</Label>
          <Input aria-label="vCPU" type="number" min={limits?.vcpu.min ?? 1} max={limits?.vcpu.max} className="mt-1" value={form.vcpu} onChange={(e) => patch({ vcpu: Number(e.target.value) })} />
        </div>
        <div>
          <Label className="text-xs font-medium text-foreground/80">Memory (MB{limits ? `, ${limits.memoire_mo.min}-${limits.memoire_mo.max}` : ""})</Label>
          <Input aria-label="Memory in MB" type="number" min={limits?.memoire_mo.min ?? 256} max={limits?.memoire_mo.max} step={128} className="mt-1" value={form.memory_mb} onChange={(e) => patch({ memory_mb: Number(e.target.value) })} />
        </div>
      </div>
      <OverallocationNote limits={limits} vcpu={form.vcpu} memoryMb={form.memory_mb} diskGb={Math.max(0, ...form.disks.map((d) => d.size_gb || 0))} />

      <div>
        <Label className="text-xs font-medium text-foreground/80">Disk controller</Label>
        <NativeSelect aria-label="Disk controller" className="mt-1 w-full" value={form.diskController || "auto"} onChange={(e) => patch({ diskController: e.target.value })}>
          <option value="auto">Automatic (recommended): {guestProfile(form) === "linux" ? "VirtIO SCSI" : "SATA"}</option>
          <option value="sata">SATA — compatible with Windows Setup</option>
          <option value="virtio-scsi">VirtIO SCSI — optimized performance</option>
        </NativeSelect>
        <p className="mt-1 text-xs text-muted-foreground">
          {diskController(form) === "sata" ? "Standard AHCI storage. Modern Windows installers include the driver." : guestProfile(form) === "windows" ? "Windows requires VirtIO SCSI drivers. Add the driver ISO in Template → Advanced: additional drivers." : "The guest OS must include VirtIO SCSI drivers. Choose SATA for installers without them."}
          {importMode && " For an imported disk, select a controller whose boot driver is already installed in the guest."}
        </p>
      </div>

      <div>
        <Label className="text-xs font-medium text-foreground/80">Disks (GB)</Label>
        <div className="mt-1 space-y-1.5">
          {form.disks.map((d, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-10 font-mono text-xs text-muted-foreground" title={i === 0 ? "System disk" : undefined}>sd{String.fromCharCode(97 + i)}</span>
              {importMode && i === 0 ? (
                <span className="flex h-9 flex-1 items-center rounded-md border border-border bg-muted px-3 text-sm text-muted-foreground">Size of the imported disk (ignored)</span>
              ) : (
                <Input aria-label={`Size of disk ${i + 1} in GB`} type="number" min={1} max={limits?.disque_go.max} value={d.size_gb} onChange={(e) => updateDisk(i, Number(e.target.value))} />
              )}
              <Button aria-label={`Remove disk ${i + 1}`} variant="secondary" size="icon" disabled={form.disks.length <= 1 || (importMode && i === 0)} onClick={() => removeDisk(i)}><X size={13} /></Button>
            </div>
          ))}
        </div>
        <Button variant="secondary" className="mt-2" onClick={addDisk} disabled={Boolean(limits) && form.disks.length >= limits.disques.max}>
          <Plus /> Add a disk
        </Button>
      </div>

      {selectablePools.length > 0 && (
        <div>
          <Label className="text-xs font-medium text-foreground/80">Storage pool</Label>
          <NativeSelect aria-label="Storage pool" className="mt-1 w-full" value={form.storagePool} onChange={(e) => patch({ storagePool: e.target.value })}>
            <option value="">Default (local)</option>
            {selectablePools.map((p) => (
              <option key={p.nom} value={p.nom}>{p.nom} ({p.type}, {p.disponible_go} GB free)</option>
            ))}
          </NativeSelect>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Choosing a shared network storage pool (netfs) then allows this VM to be protected with HA or live-migrated.
          </p>
        </div>
      )}

      {importMode ? (
        <div className="rounded-md border border-border px-3 py-2.5 text-sm text-foreground/80">
          User account not applicable: the imported disk already has its own OS and accounts (see the "Template" step).
        </div>
      ) : manualInstall ? (
        <div className="rounded-md border border-border px-3 py-2.5 text-sm text-foreground/80">
          User account not applicable: this ISO is not recognized for unattended installation, so the OS and its account will be created during the manual installation (see the "Template" step).
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-xs font-medium text-foreground/80">User</Label>
            <Input aria-label="User" className="mt-1" value={form.username} onChange={(e) => patch({ username: e.target.value })} placeholder="e.g. alice" />
          </div>
          <div>
            <Label className="text-xs font-medium text-foreground/80">Password</Label>
            <Input aria-label="Password" type="password" className="mt-1" value={form.password} onChange={(e) => patch({ password: e.target.value })} minLength={4} />
          </div>
        </div>
      )}

      <div className="rounded-md border border-border px-3 py-2.5">
        <Label className="flex items-center gap-2 text-sm text-foreground/90 font-normal">
          <Checkbox
            checked={form.autoCleanupEnabled}
            onCheckedChange={(v) => patch({ autoCleanupEnabled: !!v })}
          />
          Automatically delete this VM if it stays stopped for too long
        </Label>
        {form.autoCleanupEnabled && (
          <div className="mt-2 flex items-center gap-2 text-sm text-foreground/80 animate-in fade-in-0 duration-150">
            After
            <Input aria-label="Inactivity threshold in days"
              type="number" min={1} max={365} className="w-20"
              value={form.autoCleanupDays} onChange={(e) => patch({ autoCleanupDays: Number(e.target.value) })}
            />
            day(s) of continuous shutdown. A running VM is never affected, and an alert is sent ~24h before deletion.
          </div>
        )}
      </div>
    </div>
  );
}
