import { useEffect, useState } from "react";
import { Play, Square, Power, RotateCw, Trash2, Copy, Layers, ArrowRightLeft, ShieldCheck, ShieldOff, Timer } from "lucide-react";
import GaugeRing from "../../components/GaugeRing";
import MetricChart from "../../components/MetricChart";
import MetricsHistoryCard from "../../components/MetricsHistoryCard";
import { fetchVMMetricsHistory } from "../../api/client";
import ConfirmDialog from "../../components/ConfirmDialog";
import CompatChecks from "../../components/CompatChecks";
import ProvisioningBar from "../../components/ProvisioningBar";
import { useLiveVMMetrics } from "../../hooks/useLiveVMMetrics";
import { useProvisioningStatus } from "../../hooks/useProvisioningStatus";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { chartColors } from "../../theme/colors";
import { formatUptime, formatMo, formatKbps } from "../../utils/format";
import {
  cloneVM, createTemplateFromVM, migrateVM, fetchMigrationCheck, fetchHaProtected, enableHa, disableHa,
  fetchVMAutoCleanup, setVMAutoCleanup, disableVMAutoCleanup,
} from "../../api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export default function VMSummaryTab({ resource: vm }) {
  const [confirm, setConfirm] = useState(null); // "stop" | "force-stop" | "delete" | null
  const [migrateOpen, setMigrateOpen] = useState(false);
  const [migrateTarget, setMigrateTarget] = useState("");
  const [migrating, setMigrating] = useState(false);
  const [migrateCheck, setMigrateCheck] = useState(null); // { loading, report, error }
  const [ignoreChecks, setIgnoreChecks] = useState(false);
  const [haProtected, setHaProtected] = useState(false);
  const [haBusy, setHaBusy] = useState(false);
  const [autoCleanup, setAutoCleanup] = useState(null); // { active, inactive_days, ... } | null (loading)
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupDays, setCleanupDays] = useState(7);
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const loadAll = useInfraStore((s) => s.loadAll);
  const select = useInfraStore((s) => s.select);
  const pushToast = useInfraStore((s) => s.pushToast);
  const nodes = useInfraStore((s) => s.nodes);
  const isAdmin = useAuthStore(selectIsAdmin);
  const { data, current, error } = useLiveVMMetrics(vm?.nom, vm?.etat === "actif");
  const { status: provStatus, justFinished } = useProvisioningStatus(vm?.nom, vm?.etat === "actif");

  useEffect(() => {
    if (justFinished) {
      pushToast({ kind: "success", title: "Installation finished", message: `${vm.nom}: web SSH terminal available` });
      loadAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [justFinished]);

  // Before, a failed unattended installation (SSH timeout, VM vanished midway) stayed
  // invisible: the progress bar just silently disappeared (provisioning: false with
  // no distinction between success and failure). GET /vms/{name}/provisioning now
  // returns failed + the error explicitly in that case.
  useEffect(() => {
    if (provStatus?.failed) {
      pushToast({ kind: "error", title: "Unattended installation failed", message: provStatus.erreur || "Unknown cause" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provStatus?.failed]);

  // HA: check whether THIS VM is protected. GET /ha returns all the protected VMs,
  // with no dedicated per-VM endpoint (a short list in practice, so a client-side
  // filter is enough).
  useEffect(() => {
    if (!vm?.nom) return;
    let cancelled = false;
    fetchHaProtected()
      .then((rows) => { if (!cancelled) setHaProtected(rows.some((r) => r.vm_name === vm.nom)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [vm?.nom]);

  // Reload the status at every VM change (no global list like HA: a dedicated
  // per-VM endpoint, see app/routers/vms.py::get_vm_auto_cleanup_route).
  useEffect(() => {
    if (!vm?.nom) return;
    let cancelled = false;
    fetchVMAutoCleanup(vm.nom)
      .then((c) => { if (!cancelled) { setAutoCleanup(c); if (c.active) setCleanupDays(c.inactive_days); } })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [vm?.nom]);

  async function handleSetCleanup() {
    setCleanupBusy(true);
    try {
      await setVMAutoCleanup(vm.nom, cleanupDays);
      pushToast({ kind: "success", title: "Automatic cleanup enabled", message: `${vm.nom} -- ${cleanupDays} day(s) of inactivity` });
      setAutoCleanup({ active: true, inactive_days: cleanupDays });
      setCleanupOpen(false);
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally {
      setCleanupBusy(false);
    }
  }

  async function handleDisableCleanup() {
    setCleanupBusy(true);
    try {
      await disableVMAutoCleanup(vm.nom);
      pushToast({ kind: "success", title: "Automatic cleanup disabled", message: vm.nom });
      setAutoCleanup({ active: false });
      setCleanupOpen(false);
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally {
      setCleanupBusy(false);
    }
  }

  async function handleToggleHa() {
    setHaBusy(true);
    try {
      if (haProtected) {
        await disableHa(vm.nom);
        pushToast({ kind: "success", title: "HA protection disabled", message: vm.nom });
        setHaProtected(false);
      } else {
        await enableHa(vm.nom, vm.node);
        pushToast({ kind: "success", title: "HA protection enabled", message: vm.nom });
        setHaProtected(true);
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally {
      setHaBusy(false);
    }
  }

  const vmName = vm?.nom;
  const vmNode = vm?.node;
  useEffect(() => {
    setIgnoreChecks(false);
    if (!vmName || !migrateOpen || !migrateTarget) { setMigrateCheck(null); return undefined; }
    let alive = true;
    setMigrateCheck({ loading: true });
    fetchMigrationCheck(vmName, migrateTarget, vmNode)
      .then((report) => alive && setMigrateCheck({ report }))
      .catch((e) => alive && setMigrateCheck({ error: e.message }));
    return () => { alive = false; };
  }, [migrateOpen, migrateTarget, vmName, vmNode]);

  if (!vm) return null;
  const provisioning = provStatus?.provisioning;

  async function act(action, opts) {
    try {
      await runVMAction(vm.nom, action, opts);
      if (action === "delete") select("datacenter", null);
    } catch {
      // error already pushed as a toast by the store
    }
  }

  async function handleClone() {
    const newName = window.prompt(`Name of the copy of '${vm.nom}':`, `${vm.nom}-clone`);
    if (!newName || !newName.trim()) return;
    try {
      await cloneVM(vm.nom, newName.trim());
      pushToast({ kind: "success", title: "VM cloned", message: `${vm.nom} -> ${newName.trim()}` });
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Clone failed", message: e.message });
    }
  }


  const migrateBlocked = Boolean(migrateCheck?.report?.resume?.bloquant) && !ignoreChecks;

  async function handleMigrate() {
    if (!migrateTarget) return;
    setMigrating(true);
    try {
      await migrateVM(vm.nom, migrateTarget, vm.node, ignoreChecks);
      pushToast({ kind: "success", title: "Migration started", message: `${vm.nom} to ${migrateTarget}: follow the progress in the tasks` });
      setMigrateOpen(false);
      setMigrateTarget("");
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Migration failed", message: e.message });
    } finally {
      setMigrating(false);
    }
  }

  async function handleToTemplate() {
    const tplName = window.prompt(`Name of the template to create from '${vm.nom}':`, vm.nom);
    if (!tplName || !tplName.trim()) return;
    try {
      await createTemplateFromVM(vm.nom, tplName.trim());
      pushToast({ kind: "success", title: "Template created", message: tplName.trim() });
      select("datacenter", null); // the source VM has just disappeared (converted)
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Conversion failed", message: e.message });
    }
  }

  // The local host as a destination (even from a REMOTE node) is no longer excluded:
  // the reverse SSH trust is established automatically when each node is registered
  // (see app/core/cluster.py::ensure_reverse_trust).
  const migrationTargets = nodes.filter((n) => n.id !== vm.node && n.etat === "online");

  return (
    <div className="space-y-5">
      {isAdmin && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={vm.etat === "actif"} onClick={() => act("start")}>
            <Play /> Start
          </Button>
          <Button variant="secondary" disabled={vm.etat !== "actif"} onClick={() => setConfirm("stop")}>
            <Square /> Stop
          </Button>
          <Button
            variant="secondary"
            className="text-status-error"
            disabled={vm.etat !== "actif"}
            title="Powers the VM off immediately without waiting for the guest (equivalent to pulling the plug). Use it if the clean shutdown does not respond."
            onClick={() => setConfirm("force-stop")}
          >
            <Power /> Force stop
          </Button>
          <Button variant="secondary" disabled={vm.etat !== "actif"} onClick={() => act("restart")}>
            <RotateCw /> Restart
          </Button>
          <Button variant="secondary" disabled={vm.etat === "actif"} onClick={handleClone}>
            <Copy /> Clone
          </Button>
          <Button variant="secondary" disabled={vm.etat === "actif"} onClick={handleToTemplate}>
            <Layers /> To template
          </Button>
          <Button
            variant="secondary"
            disabled={vm.etat !== "actif" || migrationTargets.length === 0}
            title={migrationTargets.length === 0 ? "No other online node available" : "Migrate this VM to another node without shutting it down"}
            onClick={() => setMigrateOpen((o) => !o)}
          >
            <ArrowRightLeft /> Migrate
          </Button>
          <Button
            variant="secondary"
            className={haProtected ? "text-status-running" : ""}
            disabled={haBusy}
            title={haProtected ? "Disable HA protection (manual recovery if the node fails)" : "Enable HA protection: requires a disk on a shared storage pool"}
            onClick={handleToggleHa}
          >
            {haProtected ? <ShieldCheck /> : <ShieldOff />} {haBusy ? "..." : haProtected ? "HA protected" : "Protect (HA)"}
          </Button>
          <Button
            variant="secondary"
            className={autoCleanup?.active ? "text-status-warning" : ""}
            title="Automatically deletes this VM after N days of continuous shutdown (a running VM is never affected)"
            onClick={() => setCleanupOpen((o) => !o)}
          >
            <Timer /> {autoCleanup?.active ? `Auto cleanup (${autoCleanup.inactive_days}d)` : "Auto cleanup"}
          </Button>
          <Button
            variant="outline"
            className="ml-auto text-status-error border-status-error/30 hover:bg-status-error/10"
            disabled={vm.etat === "actif"}
            onClick={() => setConfirm("delete")}
          >
            <Trash2 /> Delete
          </Button>
        </div>
      )}

      {migrateOpen && (
        <Card className="flex flex-wrap items-center gap-3 p-4 animate-in fade-in-0 slide-in-from-top-1 duration-150">
          <span className="text-sm text-foreground/90">Migrate <b className="text-foreground">{vm.nom}</b> to</span>
          <Select value={migrateTarget} onValueChange={setMigrateTarget}>
            <SelectTrigger aria-label="Migration target node" className="w-auto"><SelectValue placeholder="Choose a node…" /></SelectTrigger>
            <SelectContent>
              {migrationTargets.map((n) => <SelectItem key={n.id} value={n.id}>{n.nom}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button disabled={!migrateTarget || migrating || migrateCheck?.loading || migrateBlocked} onClick={handleMigrate}>
            {migrating ? "Starting..." : "Migrate"}
          </Button>
          <Button variant="secondary" onClick={() => setMigrateOpen(false)}>Cancel</Button>
          {migrateCheck?.loading && <p className="w-full text-xs text-muted-foreground">Checking compatibility…</p>}
          {migrateCheck?.error && <p className="w-full text-xs text-status-warning">Diagnostic unavailable ({migrateCheck.error}): the migration can still be attempted, the server will check it.</p>}
          {migrateCheck?.report && <CompatChecks report={migrateCheck.report} />}
          {migrateCheck?.report?.resume?.bloquant && (
            <label className="flex w-full items-center gap-2 text-xs text-muted-foreground">
              <Checkbox checked={ignoreChecks} onCheckedChange={(v) => setIgnoreChecks(!!v)} />
              Ignore the detected blockers and try the migration anyway
            </label>
          )}
          <p className="w-full text-xs text-muted-foreground">
            Live migration: the VM keeps running during the transfer. If the disk is not on a shared pool, it is copied during the migration, which can take a while depending on its size.
          </p>
        </Card>
      )}

      {cleanupOpen && (
        <Card className="flex flex-wrap items-center gap-3 p-4 animate-in fade-in-0 slide-in-from-top-1 duration-150">
          <span className="text-sm text-foreground/90">Delete <b className="text-foreground">{vm.nom}</b> after</span>
          <Input aria-label="Inactivity threshold in days"
            type="number" min={1} max={365} className="w-20"
            value={cleanupDays} onChange={(e) => setCleanupDays(Number(e.target.value))}
          />
          <span className="text-sm text-foreground/90">day(s) of continuous shutdown</span>
          <Button disabled={cleanupBusy} onClick={handleSetCleanup}>
            {cleanupBusy ? "..." : autoCleanup?.active ? "Update" : "Enable"}
          </Button>
          {autoCleanup?.active && (
            <Button variant="outline" className="text-status-error border-status-error/30 hover:bg-status-error/10" disabled={cleanupBusy} onClick={handleDisableCleanup}>Disable</Button>
          )}
          <Button variant="secondary" onClick={() => setCleanupOpen(false)}>Close</Button>
          <p className="w-full text-xs text-muted-foreground">
            The counter only runs while the VM is stopped (restarting it resets it to zero), and an HA-protected VM is never affected. An alert is sent ~24 h before the actual deletion.
          </p>
        </Card>
      )}

      {vm.etat !== "actif" ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">VM stopped: no live metrics.</Card>
      ) : provisioning ? (
        <ProvisioningBar status={provStatus} />
      ) : (
        <>
          <Card className="grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
            <GaugeRing label="CPU" ratio={current?.cpu ?? 0} valueLabel={`${vm.vcpu} vCPU`} colorClass="text-accent-blue" />
            <GaugeRing
              label="RAM"
              ratio={current?.ram ?? 0}
              valueLabel={current ? `${formatMo(current.ramUseeMo)} / ${formatMo(current.ramAlloueeMo)}` : "--"}
              colorClass="text-accent-orange"
            />
            <div className="flex flex-col items-center justify-center gap-1 text-center">
              <div className="text-sm text-foreground/90">
                {(current?.disques || []).map((d) => (
                  <div key={d.cible}>{d.cible} : {formatKbps(d.lecture_ko_s)} read / {formatKbps(d.ecriture_ko_s)} written</div>
                ))}
              </div>
              <div className="text-xs text-muted-foreground mt-1">Disks (instantaneous throughput)</div>
            </div>
          </Card>

          {error && <div className="text-xs text-status-error">Error reading the metrics: {error}</div>}

          <Card className="p-5">
            <h3 className="text-sm font-semibold text-foreground">CPU & RAM (current session)</h3>
            <div className="mt-3">
              <MetricChart
                data={data}
                series={[
                  { key: "cpu", label: "CPU", color: chartColors.cpu },
                  { key: "ram", label: "RAM", color: chartColors.ram },
                ]}
                yFormatter={(v) => `${Math.round(v * 100)}%`}
              />
            </div>
          </Card>

          <Card className="p-5">
            <h3 className="text-sm font-semibold text-foreground">Network (KB/s)</h3>
            <div className="mt-3">
              <MetricChart
                data={data}
                series={[
                  { key: "netIn", label: "Incoming", color: chartColors.netIn },
                  { key: "netOut", label: "Outgoing", color: chartColors.netOut },
                ]}
                yFormatter={(v) => formatKbps(v)}
              />
            </div>
          </Card>
        </>
      )}

      <MetricsHistoryCard title="CPU history (persisted)" fetcher={(range) => fetchVMMetricsHistory(vm.nom, range)} />

      <Card className="p-5">
        <h3 className="mb-3 text-sm font-semibold text-foreground">Status</h3>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-muted-foreground text-xs">Uptime</dt><dd className="text-foreground">{formatUptime(vm.uptime_s)}</dd></div>
          <div><dt className="text-muted-foreground text-xs">IP address</dt><dd className="text-foreground">{vm.ip || "--"}</dd></div>
          <div><dt className="text-muted-foreground text-xs">Detected OS</dt><dd className="text-foreground">{vm.os || "--"}</dd></div>
          <div><dt className="text-muted-foreground text-xs">SSH user</dt><dd className="text-foreground">{vm.utilisateur_ssh || "unknown"}</dd></div>
          <div>
            <dt className="text-muted-foreground text-xs">Automatic cleanup</dt>
            <dd className={autoCleanup?.active ? "text-status-warning" : "text-foreground"}>
              {autoCleanup?.active ? `Active: ${autoCleanup.inactive_days} d of shutdown` : "Inactive"}
            </dd>
          </div>
        </dl>
      </Card>

      <ConfirmDialog
        open={confirm === "stop"}
        title={`Stop '${vm.nom}'?`}
        message="A clean shutdown (ACPI) will be attempted. If the guest does not respond (e.g. a frozen screen), the VM stays running: use 'Force stop' in that case."
        confirmLabel="Stop"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("stop"); }}
      />
      <ConfirmDialog
        open={confirm === "force-stop"}
        title={`Force stop '${vm.nom}'?`}
        message="Powers the VM off immediately, as if the power were unplugged: no clean system shutdown, and unsaved data may be lost. Only use it if the normal shutdown does not work."
        confirmLabel="Force stop"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("stop", { force: true }); }}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        title={`Delete '${vm.nom}'?`}
        message="This action is irreversible: the VM and its disk will be permanently deleted."
        confirmLabel="Delete"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("delete"); }}
      />
    </div>
  );
}
