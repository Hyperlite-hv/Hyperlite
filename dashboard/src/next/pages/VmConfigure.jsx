import { useCallback, useEffect, useState } from "react";
import {
  fetchVMDisks, attachDisk, detachDisk, createVolume, fetchVolumes, fetchVMNetwork, attachInterface, detachInterface, fetchNetworks,
  fetchVMFirewall, setVMFirewall, fetchVMLimits, setVMLimits,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useHostLimits } from "../../hooks/useHostLimits";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import DriversIsoControl from "../../components/DriversIsoControl";
import FirewallRulesEditor from "../../components/FirewallRulesEditor";
import { EmptyState, ErrorState } from "../components/States";

// First free SCSI letter (sda…sdz); null when none is left.
function nextScsiDev(disks) {
  const used = new Set(disks.filter((d) => /^sd[a-z]$/.test(d.cible)).map((d) => d.cible));
  for (const l of "abcdefghijklmnopqrstuvwxyz") if (!used.has(`sd${l}`)) return `sd${l}`;
  return null;
}
const freshName = (vm) => `${vm}-disk-${Date.now().toString().slice(-5)}`;
const VOL_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$/;

function Disks({ vmName, admin, t }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const limits = useHostLimits();
  const [disks, setDisks] = useState(null);
  const [error, setError] = useState(null);
  const [volumes, setVolumes] = useState([]);
  const [source, setSource] = useState("__new__");
  const [name, setName] = useState(() => freshName(vmName));
  const [size, setSize] = useState(5);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try { const [d, v] = await Promise.all([fetchVMDisks(vmName), fetchVolumes("default")]); setDisks(Array.isArray(d) ? d : []); setVolumes((Array.isArray(v) ? v : []).filter((x) => !x.utilise)); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, [vmName]);
  useEffect(() => { setName(freshName(vmName)); reload(); }, [vmName, reload]);

  if (error && disks == null) return <ErrorState message={error} onRetry={reload} />;
  if (disks == null) return <p className="nx-muted" role="status">{t("loading")}</p>;
  const nextDev = nextScsiDev(disks);
  const maxGb = limits?.disque_go?.max;
  const sizeBad = source === "__new__" && (!Number.isInteger(Number(size)) || Number(size) < 1 || (maxGb && Number(size) > maxGb));
  const nameBad = source === "__new__" && !VOL_RE.test(name.trim());

  async function detach(d) {
    if (!(await confirmAction({ title: t("vh.detachTitle", { dev: d.cible }), message: t("vh.detachMsg"), confirmLabel: t("vh.detach"), danger: true }))) return;
    setBusy(true);
    try { await detachDisk(vmName, d.cible); pushToast({ kind: "success", title: t("vh.detached"), message: d.cible }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("vh.detachFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function attach(e) {
    e.preventDefault();
    if (!nextDev || sizeBad || nameBad) return;
    setBusy(true);
    let created = null;
    try {
      let vol = source;
      if (source === "__new__") { const c = await createVolume("default", name.trim(), Number(size)); vol = c.nom; created = c.nom; }
      await attachDisk(vmName, vol, nextDev);
      pushToast({ kind: "success", title: t("vh.attached"), message: `${vol} → ${nextDev}` });
      setName(freshName(vmName)); await reload();
    } catch (er) {
      // The volume may exist without being attached: list it so the attach can be retried.
      pushToast({ kind: "error", title: t("vh.attachFailed"), message: created ? `${errorMessage(er)} — ${t("vh.orphan", { name: created })}` : errorMessage(er) });
      if (created) { setSource(created); await reload(); }
    } finally { setBusy(false); }
  }

  return (
    <section className="nx-card" aria-labelledby="vh-disks">
      <div className="nx-cardhead"><h2 id="vh-disks">{t("vh.disks")} <span className="nx-count">{disks.length}</span></h2></div>
      {disks.length === 0 ? <EmptyState title={t("vh.noDisks")} /> : (
        <div className="nx-tablewrap">
          <table className="nx-table">
            <thead><tr><th scope="col">{t("vh.device")}</th><th scope="col">Bus</th><th scope="col">{t("vh.source")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {disks.map((d) => (
                <tr key={d.cible}>
                  <th scope="row" className="nx-mono">{d.cible}</th>
                  <td>{d.bus || <span className="nx-muted">{t("ns.notReported")}</span>}</td>
                  <td className="nx-mono">{d.source || <span className="nx-muted">{t("vh.emptyDrive")}</span>}</td>
                  <td className="nx-num">{admin && d.type !== "cdrom" && d.cible !== "vda" && d.cible !== "sda" && <button type="button" className="nx-btn nx-btn--danger" disabled={busy} aria-label={`Detach disk ${d.cible}`} onClick={() => detach(d)}>{t("vh.detach")}</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {admin && <DriversIsoControl vmName={vmName} onChanged={reload} />}
      {admin && (
        <form className="nx-form" onSubmit={attach} noValidate>
          <h3 className="nx-subhead">{t("vh.addDevice")}</h3>
          <label>{t("vh.disk")}<select className="nx-input" aria-label="Disk to attach" value={source} onChange={(e) => setSource(e.target.value)}><option value="__new__">{t("vh.newDisk")}</option>{volumes.map((v) => <option key={v.nom} value={v.nom}>{v.nom} ({v.capacite_go} GB)</option>)}</select></label>
          {source === "__new__" && (
            <div className="nx-formgrid">
              <label>{t("vh.volName")}<input className="nx-input" aria-label="volume name" value={name} onChange={(e) => setName(e.target.value)} aria-invalid={nameBad || undefined} />{nameBad && <span className="nx-hint nx-hint--error">{t("vh.volRule")}</span>}</label>
              <label>{t("vh.sizeGb")}<input className="nx-input" aria-label="New disk size in GB" type="number" min={1} max={maxGb} value={size} onChange={(e) => setSize(e.target.value)} aria-invalid={sizeBad || undefined} />{sizeBad && <span className="nx-hint nx-hint--error">{t("vh.sizeRule", { max: maxGb ?? "…" })}</span>}</label>
            </div>
          )}
          <div className="nx-formactions">
            <span className="nx-hint" style={{ marginRight: "auto" }}>{nextDev ? t("vh.willBe", { dev: nextDev }) : t("vh.noLetter")}</span>
            <button type="submit" className="nx-btn nx-btn--primary" disabled={busy || !nextDev || sizeBad || nameBad}>{t("vh.attach")}</button>
          </div>
        </form>
      )}
    </section>
  );
}

function Interfaces({ vmName, admin, t }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [networks, setNetworks] = useState([]);
  const [net, setNet] = useState("");
  const [vlan, setVlan] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try { const [i, n] = await Promise.all([fetchVMNetwork(vmName), fetchNetworks()]); setInfo(i); setNetworks(Array.isArray(n) ? n : []); setNet((p) => p || n?.[0]?.nom || ""); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, [vmName]);
  useEffect(() => { reload(); }, [reload]);

  if (error && !info) return <ErrorState message={error} onRetry={reload} />;
  if (!info) return <p className="nx-muted" role="status">{t("loading")}</p>;
  const ifaces = info.interfaces || [];
  const vlanBad = vlan !== "" && (!Number.isInteger(Number(vlan)) || Number(vlan) < 1 || Number(vlan) > 4094);

  async function detach(i) {
    if (!(await confirmAction({ title: t("vh.ifDetachTitle", { mac: i.mac }), message: t("vh.ifDetachMsg"), confirmLabel: t("sec.remove"), danger: true }))) return;
    setBusy(true);
    try { await detachInterface(vmName, i.mac); pushToast({ kind: "success", title: t("vh.ifDetached"), message: i.mac }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("vh.detachFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function attach(e) {
    e.preventDefault();
    if (!net || vlanBad) return;
    setBusy(true);
    try { await attachInterface(vmName, net, vlan ? Number(vlan) : null); pushToast({ kind: "success", title: t("vh.ifAdded"), message: net }); setVlan(""); await reload(); }
    catch (er) { pushToast({ kind: "error", title: t("sec.addFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }

  return (
    <section className="nx-card" aria-labelledby="vh-net">
      <div className="nx-cardhead"><h2 id="vh-net">{t("vh.interfaces")} <span className="nx-count">{ifaces.length}</span></h2></div>
      <div className="nx-tablewrap">
        <table className="nx-table">
          <thead><tr><th scope="col">{t("vh.network")}</th><th scope="col">MAC</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
          <tbody>
            {ifaces.map((i) => (
              <tr key={i.mac}>
                <th scope="row" className="nx-mono">{i.reseau || <span className="nx-muted">{t("ns.notReported")}</span>}</th>
                <td className="nx-mono">{i.mac}</td>
                <td className="nx-num">{admin && ifaces.length > 1 && <button type="button" className="nx-btn nx-btn--danger" disabled={busy} aria-label={`Remove interface ${i.mac}`} onClick={() => detach(i)}>{t("sec.remove")}</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {admin && (
        <form className="nx-form" onSubmit={attach} noValidate>
          <div className="nx-formgrid">
            <label>{t("vh.network")}<select className="nx-input" aria-label="Network to attach" value={net} onChange={(e) => setNet(e.target.value)}>{networks.map((n) => <option key={n.nom} value={n.nom}>{n.nom} ({n.type})</option>)}</select></label>
            <label>VLAN<input className="nx-input" aria-label="VLAN (optional)" type="number" min={1} max={4094} value={vlan} placeholder={t("vh.optional")} onChange={(e) => setVlan(e.target.value)} aria-invalid={vlanBad || undefined} />{vlanBad && <span className="nx-hint nx-hint--error">{t("vh.vlanRule")}</span>}</label>
          </div>
          <div className="nx-formactions"><button type="submit" className="nx-btn nx-btn--primary" disabled={busy || !net || vlanBad}>{t("vh.addIf")}</button></div>
        </form>
      )}
    </section>
  );
}

// Hardware: processor and memory (changed in Options) and disks with ISO drivers.
export function VmHardwarePage({ resource: vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  if (!vm) return null;
  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="vh-cpu">
        <div className="nx-cardhead"><h2 id="vh-cpu">{t("vh.compute")}</h2></div>
        <dl className="nx-dl"><dt>{t("vh.processor")}</dt><dd className="nx-mono">{vm.vcpu} vCPU</dd><dt>{t("ct.memory")}</dt><dd className="nx-mono">{formatSizeMb(vm.memoire_mo, lang)}</dd></dl>
        <p className="nx-hint">{t("vh.computeHelp")}</p>
      </section>
      <Disks vmName={vm.nom} admin={admin} t={t} />
    </div>
  );
}

// Network: interfaces and the VM firewall.
export function VmNetworkPage({ resource: vm }) {
  const t = useT();
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const fetchFw = useCallback(() => fetchVMFirewall(vm?.nom), [vm?.nom]);
  const saveFw = useCallback((c) => setVMFirewall(vm?.nom, c), [vm?.nom]);
  if (!vm) return null;
  return (
    <div className="nx-ns">
      <Interfaces vmName={vm.nom} admin={admin} t={t} />
      <FirewallRulesEditor title={t("vh.firewall")} fetchConfig={fetchFw} saveConfig={saveFw} isAdmin={admin} />
    </div>
  );
}

// ---- Options: resources (stopped VM) and cgroup limits (live) ----------------------------------------------
const intIn = (v, min, max) => v !== "" && Number.isInteger(Number(v)) && Number(v) >= min && (max == null || Number(v) <= max);

export function VmOptionsPage({ resource: vm }) {
  const t = useT();
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const updateVMResources = useInfraStore((s) => s.updateVMResources);
  const limits = useHostLimits();
  const [vcpu, setVcpu] = useState(String(vm?.vcpu ?? 1));
  const [mem, setMem] = useState(String(vm?.memoire_mo ?? 512));
  const [busy, setBusy] = useState(false);
  useEffect(() => { setVcpu(String(vm?.vcpu ?? 1)); setMem(String(vm?.memoire_mo ?? 512)); }, [vm?.nom, vm?.vcpu, vm?.memoire_mo]);
  if (!vm) return null;

  const running = vm.etat === "actif";
  const vMin = limits?.vcpu.min ?? 1, vMax = limits?.vcpu.max, mMin = limits?.memoire_mo.min ?? 256, mMax = limits?.memoire_mo.max;
  const vBad = !intIn(vcpu, vMin, vMax), mBad = !intIn(mem, mMin, mMax);
  const dirty = Number(vcpu) !== vm.vcpu || Number(mem) !== vm.memoire_mo;

  async function save(e) {
    e.preventDefault();
    if (vBad || mBad || running) return;
    setBusy(true);
    try { await updateVMResources(vm.nom, { vcpu: Number(vcpu), memory_mb: Number(mem) }); } catch { /* the store already shows the error */ } finally { setBusy(false); }
  }

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="vo-res">
        <div className="nx-cardhead"><h2 id="vo-res">{t("vo.resources")}</h2></div>
        {running && <p className="nx-notice" role="status">{t("vo.stopFirst")}</p>}
        <form className="nx-form" onSubmit={save} noValidate>
          <div className="nx-formgrid">
            <label>vCPU<input className="nx-input" aria-label="vCPU count" type="number" min={vMin} max={vMax} disabled={!admin || running} value={vcpu} onChange={(e) => setVcpu(e.target.value)} aria-invalid={vBad || undefined} /><span className={vBad ? "nx-hint nx-hint--error" : "nx-hint"}>{t("vo.range", { min: vMin, max: vMax ?? "…" })}</span></label>
            <label>{t("ct.memory")} (MB)<input className="nx-input" aria-label="Memory in MB" type="number" min={mMin} max={mMax} step={128} disabled={!admin || running} value={mem} onChange={(e) => setMem(e.target.value)} aria-invalid={mBad || undefined} /><span className={mBad ? "nx-hint nx-hint--error" : "nx-hint"}>{t("vo.range", { min: mMin, max: mMax ?? "…" })}</span></label>
          </div>
          {admin && <div className="nx-formactions"><button type="submit" className="nx-btn nx-btn--primary" disabled={!dirty || busy || running || vBad || mBad}>{t("sso.save")}</button></div>}
        </form>
      </section>
      <LimitsCard vm={vm} admin={admin} t={t} />
    </div>
  );
}

// GET/PUT /vms/{name}/limits (cgroups through libvirt): applies live, no need to stop the VM.
function LimitsCard({ vm, admin, t }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [limits, setLimits] = useState(null);
  const [error, setError] = useState(null);
  const [shares, setShares] = useState("1024");
  const [cpu, setCpu] = useState("");
  const [ram, setRam] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { const l = await fetchVMLimits(vm.nom); setLimits(l); setShares(String(l.cpu_shares)); setCpu(l.cpu_limit_pct ?? ""); setRam(l.mem_hard_limit_mb ?? ""); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, [vm.nom]);
  useEffect(() => { load(); }, [load]);

  if (error && !limits) return <ErrorState message={error} onRetry={load} />;
  if (!limits) return <p className="nx-muted" role="status">{t("loading")}</p>;
  const bad = { shares: !intIn(shares, 2, 262144), cpu: cpu !== "" && !intIn(cpu, 1, 100), ram: ram !== "" && !intIn(ram, 64) };
  const dirty = Number(shares) !== limits.cpu_shares || (cpu === "" ? null : Number(cpu)) !== (limits.cpu_limit_pct ?? null) || (ram === "" ? null : Number(ram)) !== (limits.mem_hard_limit_mb ?? null);

  async function save(e) {
    e.preventDefault();
    if (bad.shares || bad.cpu || bad.ram) return;
    setBusy(true);
    try {
      const u = await setVMLimits(vm.nom, { cpu_shares: Number(shares), cpu_limit_pct: cpu === "" ? null : Number(cpu), mem_hard_limit_mb: ram === "" ? null : Number(ram) });
      setLimits(u); pushToast({ kind: "success", title: t("vo.applied"), message: vm.nom });
    } catch (er) { pushToast({ kind: "error", title: t("vo.applyFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }
  const field = (label, help, err, msg, props) => <label>{label}<input className="nx-input" disabled={!admin} aria-invalid={err || undefined} {...props} /><span className={err ? "nx-hint nx-hint--error" : "nx-hint"}>{err ? msg : help}</span></label>;

  return (
    <section className="nx-card" aria-labelledby="vo-lim">
      <div className="nx-cardhead"><h2 id="vo-lim">{t("vo.limits")}</h2></div>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("vo.limitsHelp")}</p>
      <form className="nx-form" onSubmit={save} noValidate>
        <div className="nx-formgrid">
          {field(t("vo.shares"), t("vo.sharesHelp"), bad.shares, t("vo.sharesRule"), { "aria-label": "CPU shares", type: "number", min: 2, max: 262144, value: shares, onChange: (e) => setShares(e.target.value) })}
          {field(t("vo.cpuCap"), t("vo.cpuCapHelp"), bad.cpu, t("vo.cpuCapRule"), { "aria-label": "Max CPU limit percent per vCPU", type: "number", min: 1, max: 100, value: cpu, placeholder: t("vo.unlimited"), onChange: (e) => setCpu(e.target.value) })}
          {field(t("vo.ramCap"), t("vo.ramCapHelp"), bad.ram, t("vo.ramCapRule"), { "aria-label": "RAM limit in MB", type: "number", min: 64, value: ram, placeholder: t("vo.unlimited"), onChange: (e) => setRam(e.target.value) })}
        </div>
        {admin && <div className="nx-formactions"><button type="submit" className="nx-btn nx-btn--primary" disabled={!dirty || busy || bad.shares || bad.cpu || bad.ram}>{t("vo.apply")}</button></div>}
      </form>
    </section>
  );
}
