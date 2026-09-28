import { useCallback, useEffect, useState } from "react";
import { Info, Plus, Trash2, Zap } from "lucide-react";
import {
  fetchVMDisks, attachDisk, detachDisk, resizeDisk, moveDisk, createVolume, fetchVolumes, fetchVMNetwork, attachInterface, detachInterface, fetchNetworks,
  fetchVMFirewall, setVMFirewall, fetchVMLimits, setVMLimits, fetchHostDevices, fetchVMHostDevices, attachVMHostDevice, detachVMHostDevice, fetchIsoTemplates, mountVMDriversIso, ejectVMDriversIso,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useHostLimits } from "../../hooks/useHostLimits";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeGb } from "../lib/format";
import { ErrorState } from "../components/States";
import { Card, Chip, Field, SideDrawer, Loading, TableWrap } from "../components/ui";
import FirewallCard from "../components/FirewallCard";

// First free SCSI letter (sda…sdz); null when none is left.
function nextScsiDev(disks) {
  const used = new Set(disks.filter((d) => /^sd[a-z]$/.test(d.cible)).map((d) => d.cible));
  for (const l of "abcdefghijklmnopqrstuvwxyz") if (!used.has(`sd${l}`)) return `sd${l}`;
  return null;
}
const freshName = (vm) => `${vm}-disk-${Date.now().toString().slice(-5)}`;
const VOL_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$/;
const intIn = (v, min, max) => v !== "" && Number.isInteger(Number(v)) && Number(v) >= min && (max == null || Number(v) <= max);
const OS_SUGGESTIONS = ["Debian 12", "Ubuntu 24.04", "Rocky Linux 9", "Windows Server 2025", "Windows 11", "Alpine Linux"];

// Processor, memory and declared OS, editable in place. vCPU and memory need the VM stopped (the backend refuses
// otherwise); the OS label is only a description and can change at any time.
function ComputeCard({ vm, admin }) {
  const t = useT();
  const limits = useHostLimits();
  const updateVMResources = useInfraStore((s) => s.updateVMResources);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [vcpu, setVcpu] = useState(String(vm.vcpu ?? 1));
  const [mem, setMem] = useState(String(vm.memoire_mo ?? 512));
  const [os, setOs] = useState(vm.os || "");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setVcpu(String(vm.vcpu ?? 1)); setMem(String(vm.memoire_mo ?? 512)); setOs(vm.os || ""); }, [vm.nom, vm.vcpu, vm.memoire_mo, vm.os]);
  const running = vm.etat === "actif";
  const vMin = limits?.vcpu.min ?? 1, vMax = limits?.vcpu.max, mMin = limits?.memoire_mo.min ?? 256, mMax = limits?.memoire_mo.max;
  const vBad = !intIn(vcpu, vMin, vMax), mBad = !intIn(mem, mMin, mMax);
  const resDirty = Number(vcpu) !== vm.vcpu || Number(mem) !== vm.memoire_mo;
  const osDirty = os.trim() !== (vm.os || "") && os.trim() !== "";
  const blocked = (resDirty && (running || vBad || mBad)) || (!resDirty && !osDirty);

  async function save() {
    if (blocked) return;
    setBusy(true);
    try {
      const payload = { ...(resDirty ? { vcpu: Number(vcpu), memory_mb: Number(mem) } : {}), ...(osDirty ? { os_label: os.trim() } : {}) };
      await updateVMResources(vm.nom, payload);
      pushToast({ kind: "success", title: t("vh.saved"), message: vm.nom });
      refreshAll?.();
    } catch { /* the store already shows the error */ } finally { setBusy(false); }
  }
  return (
    <Card title={t("vh.compute")}>
      {running && <p className="nx-f-h" style={{ margin: "0 0 var(--space-3)" }}>{t("vo.stopFirst")}</p>}
      <div className="nx-fg nx-fg--3">
        <Field label="vCPU" unit="vCPU" error={vBad ? t("vo.range", { min: vMin, max: vMax ?? "∞" }) : null} hint={t("vo.range", { min: vMin, max: vMax ?? "∞" })}>
          {(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.vcpu_count")} type="number" min={vMin} max={vMax} disabled={!admin || running} value={vcpu} onChange={(e) => setVcpu(e.target.value)} />}
        </Field>
        <Field label={t("ct.memory")} unit={lang() === "fr" ? "Mo" : "MB"} error={mBad ? t("vo.range", { min: mMin, max: mMax ?? "∞" }) : null} hint={t("vo.range", { min: mMin, max: mMax ?? "∞" })}>
          {(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.memory_in_mb")} type="number" min={mMin} max={mMax} step={128} disabled={!admin || running} value={mem} onChange={(e) => setMem(e.target.value)} />}
        </Field>
        <Field label={t("vh.osLabel")} hint={t("vh.osHint")}>
          {(p) => <><input {...p} className="nx-inp" aria-label={t("a11y.guest_os")} list="vh-os-list" disabled={!admin} value={os} maxLength={64} onChange={(e) => setOs(e.target.value)} /><datalist id="vh-os-list">{OS_SUGGESTIONS.map((o) => <option key={o} value={o} />)}</datalist></>}
        </Field>
      </div>
      {admin && (
        <div className="nx-fa">
          <span className="nx-fa-l"><Info size={14} aria-hidden="true" />{t("vh.nextBoot")}</span>
          <button type="button" className="nx-btn" disabled={busy || blocked} onClick={save}>{t("sso.save")}</button>
        </div>
      )}
    </Card>
  );
}
const lang = () => useLangStore.getState().lang;

function AddDiskDrawer({ open, onClose, vmName, disks, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const storagePools = useInfraStore((s) => s.storagePools);
  const limits = useHostLimits();
  const pools = storagePools.filter((p) => p.node === "local" && p.type !== "zfs" && p.etat === "actif");
  const [pool, setPool] = useState("default");
  const [volumes, setVolumes] = useState([]);
  const [source, setSource] = useState("__new__");
  const [name, setName] = useState(() => freshName(vmName));
  const [size, setSize] = useState("20");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setName(freshName(vmName)); fetchVolumes(pool).then((v) => setVolumes((Array.isArray(v) ? v : []).filter((x) => !x.utilise))).catch(() => setVolumes([])); } }, [open, pool, vmName]);
  const nextDev = nextScsiDev(disks || []);
  const maxGb = limits?.disque_go?.max;
  const sizeBad = source === "__new__" && (!Number.isInteger(Number(size)) || Number(size) < 1 || (maxGb && Number(size) > maxGb));
  const nameBad = source === "__new__" && !VOL_RE.test(name.trim());

  async function attach() {
    if (!nextDev || sizeBad || nameBad) return;
    setBusy(true);
    let created = null;
    try {
      let vol = source;
      if (source === "__new__") { const c = await createVolume(pool, name.trim(), Number(size)); vol = c.nom; created = c.nom; }
      await attachDisk(vmName, vol, nextDev, pool);
      pushToast({ kind: "success", title: t("vh.attached"), message: `${vol} → ${nextDev}` });
      onDone(); onClose();
    } catch (er) {
      // The volume may exist without being attached: it is now listed as a free volume so the attach can be retried.
      pushToast({ kind: "error", title: t("vh.attachFailed"), message: created ? `${errorMessage(er)} — ${t("vh.orphan", { name: created })}` : errorMessage(er) });
      if (created) { setSource(created); onDone(); }
    } finally { setBusy(false); }
  }
  const pick = pools.find((p) => p.nom === pool);
  return (
    <SideDrawer open={open} title={t("vh.addDisk")} onClose={onClose} busy={busy} footer={<>
      <span className="nx-f-h" style={{ marginRight: "auto" }}>{nextDev ? t("vh.willBe", { dev: nextDev }) : t("vh.noLetter")}</span>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !nextDev || sizeBad || nameBad} onClick={attach}>{t("vh.attachBtn")}</button>
    </>}>
      <Field label={t("vh.pool")}>{(p) => <select {...p} className="nx-inp" aria-label={t("a11y.pool")} value={pool} onChange={(e) => { setPool(e.target.value); setSource("__new__"); }}>{(pools.length ? pools : [{ nom: "default" }]).map((x) => <option key={x.nom} value={x.nom}>{x.nom}{x.disponible_go != null ? ` · ${formatSizeGb(x.disponible_go, lang())} ${t("stor.free").toLowerCase()}` : ""}</option>)}</select>}</Field>
      <Field label={t("vh.disk")}>{(p) => <select {...p} className="nx-inp" aria-label={t("a11y.disk_to_attach")} value={source} onChange={(e) => setSource(e.target.value)}><option value="__new__">{t("vh.newDisk")}</option>{volumes.map((v) => <option key={v.nom} value={v.nom}>{v.nom} ({v.capacite_go} GB)</option>)}</select>}</Field>
      {source === "__new__" && <>
        <Field label={t("vh.volName")} error={nameBad ? t("vh.volRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.volume_name")} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label={t("vh.size")} unit={lang() === "fr" ? "Go" : "GB"} error={sizeBad ? t("vh.sizeRule", { max: maxGb ?? "∞" }) : null} hint={pick?.disponible_go != null ? t("vh.poolFree", { n: formatSizeGb(pick.disponible_go, lang()) }) : null}>
          {(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.new_disk_size_in_gb")} type="number" min={1} max={maxGb} value={size} onChange={(e) => setSize(e.target.value)} />}
        </Field>
      </>}
    </SideDrawer>
  );
}

// Grow one disk to a new total size. Growing never destroys data, so the explicit button in the drawer is the
// confirmation; the backend refuses a shrink and the disks it cannot grow (iSCSI LUNs).
function ResizeDiskDrawer({ disk, onClose, vm, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const limits = useHostLimits();
  const [size, setSize] = useState("");
  const [busy, setBusy] = useState(false);
  const minGb = disk?.taille_go != null ? Math.floor(disk.taille_go) + 1 : 1;
  useEffect(() => { if (disk) setSize(String(minGb)); }, [disk, minGb]);
  const maxGb = limits?.disque_go?.max;
  // Already at the per-disk limit: no size is valid, so say why instead of showing an impossible range.
  const atLimit = maxGb != null && minGb > maxGb;
  const limitOrigin = limits?.disque_go?.source === "configuration" ? limits.disque_go.variable : limits?.disque_go?.detail;
  const sizeBad = atLimit || !intIn(size, minGb, maxGb);
  async function submit() {
    if (sizeBad) return;
    setBusy(true);
    try {
      const r = await resizeDisk(vm.nom, disk.cible, Number(size));
      pushToast({ kind: "success", title: t("vh.resized"), message: `${disk.cible} → ${formatSizeGb(r.taille_go, lang())}` });
      onDone(); onClose();
    } catch (e) { pushToast({ kind: "error", title: t("vh.resizeFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  const unit = lang() === "fr" ? "Go" : "GB";
  return (
    <SideDrawer open={!!disk} title={disk ? t("vh.resizeTitle", { dev: disk.cible }) : ""} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || sizeBad} onClick={submit}>{t("vh.resizeBtn")}</button>
    </>}>
      {disk && <>
        <p className="nx-f-h" style={{ margin: 0 }}>{t("vh.resizeCurrent", { n: disk.taille_go != null ? formatSizeGb(disk.taille_go, lang()) : "—" })}</p>
        {atLimit ? <p className="nx-notice nx-notice--warning" role="status" style={{ margin: 0 }}>{t("vh.resizeAtLimit", { max: formatSizeGb(maxGb, lang()), origin: limitOrigin || "—" })}</p> : (
          <Field label={t("vh.resizeNew")} unit={unit} error={sizeBad ? t("vh.resizeRule", { min: minGb, max: maxGb ?? "∞" }) : null}>
            {(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.new_size_in_gb")} type="number" min={minGb} max={maxGb} value={size} onChange={(e) => setSize(e.target.value)} />}
          </Field>
        )}
        {vm.etat === "actif" && <p className="nx-f-h" style={{ margin: 0 }}>{t("vh.resizeLive")}</p>}
        <p className="nx-f-h" style={{ margin: 0 }}>{t("vh.resizeGuest")}</p>
      </>}
    </SideDrawer>
  );
}

// Move one disk to another directory or NFS pool. The server checks everything again and runs the copy as a
// task; the source file is kept unless the admin asks to delete it.
function MoveDiskDrawer({ disk, onClose, vm, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const storagePools = useInfraStore((s) => s.storagePools);
  const targets = storagePools.filter((p) => p.node === "local" && ["dir", "netfs"].includes(p.type) && p.etat === "actif" && p.nom !== disk?.pool);
  const [pool, setPool] = useState("");
  const [deleteSource, setDeleteSource] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (disk) { setPool(""); setDeleteSource(false); } }, [disk]);
  async function submit() {
    setBusy(true);
    try {
      await moveDisk(vm.nom, disk.cible, pool, deleteSource);
      pushToast({ kind: "success", title: t("vh.moveStarted"), message: `${disk.cible} → ${pool}` });
      onClose();
      setTimeout(onDone, 3000);
    } catch (e) { pushToast({ kind: "error", title: t("vh.moveFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <SideDrawer open={!!disk} title={disk ? t("vh.moveTitle", { dev: disk.cible }) : ""} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !pool} onClick={submit}>{t("vh.moveBtn")}</button>
    </>}>
      {disk && <>
        <p className="nx-f-h" style={{ margin: 0 }}>{t("vh.moveFrom", { pool: disk.pool || "—" })}</p>
        {targets.length === 0 ? <p className="nx-notice nx-notice--warning" role="status" style={{ margin: 0 }}>{t("vh.moveNoTarget")}</p> : (
          <Field label={t("vh.moveTo")}>
            {(p) => <select {...p} className="nx-inp" aria-label={t("a11y.move_destination_pool")} value={pool} onChange={(e) => setPool(e.target.value)}>
              <option value="">{t("vh.moveChoose")}</option>
              {targets.map((x) => <option key={x.nom} value={x.nom}>{x.nom} ({x.type === "netfs" ? "NFS" : t("vh.moveDir")}{x.disponible_go != null ? ` · ${formatSizeGb(x.disponible_go, lang())} ${t("stor.free").toLowerCase()}` : ""})</option>)}
            </select>}
          </Field>
        )}
        <label className="nx-check"><input type="checkbox" checked={deleteSource} onChange={(e) => setDeleteSource(e.target.checked)} /> {t("vh.moveDelete")}</label>
        <p className="nx-f-h" style={{ margin: 0 }}>{t(vm.etat === "actif" ? "vh.moveLive" : "vh.moveCold")}</p>
      </>}
    </SideDrawer>
  );
}

function DriversCard({ vmName, onChanged }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [isos, setIsos] = useState([]);
  const [iso, setIso] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { fetchIsoTemplates().then((r) => setIsos(Array.isArray(r) ? r : [])).catch(() => setIsos([])); }, []);
  async function change(eject) {
    setBusy(true);
    try {
      if (eject) await ejectVMDriversIso(vmName); else await mountVMDriversIso(vmName, iso);
      pushToast({ kind: "success", title: t(eject ? "vh.drvEjected" : "vh.drvMounted") });
      await onChanged();
    } catch (e) { pushToast({ kind: "error", title: t("vh.drvFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <Card title={t("vh.drivers")}>
      <div className="nx-inline">
        <select className="nx-inp" style={{ flex: 1, minWidth: "12rem" }} aria-label={t("vh.drvIso")} value={iso} onChange={(e) => setIso(e.target.value)}>
          <option value="">{t("vh.drvChoose")}</option>
          {isos.map((i) => <option key={i.nom} value={i.nom}>{i.nom}</option>)}
        </select>
        <button type="button" className="nx-btn" disabled={busy || !iso} onClick={() => change(false)}>{t("vh.drvInsert")}</button>
        <button type="button" className="nx-btn nx-btn--ghost" disabled={busy} onClick={() => change(true)}>{t("vh.drvEject")}</button>
      </div>
      <p className="nx-f-h" style={{ margin: "var(--space-2) 0 0" }}>{t("vh.drvHelp")}</p>
    </Card>
  );
}

// ---- Host devices: PCI and USB passthrough (administrators only) ----------------------------------------
function HostDevicesCard({ vm }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const running = vm.etat === "actif";
  const reload = useCallback(async () => {
    try { setList(await fetchVMHostDevices(vm.nom)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [vm.nom]);
  useEffect(() => { reload(); }, [reload]);
  async function remove(d) {
    if (!(await confirmAction({ title: t("hd.removeTitle", { name: d.produit || d.id }), message: t("hd.removeMsg"), confirmLabel: t("hd.remove"), danger: true }))) return;
    setBusy(true);
    try { await detachVMHostDevice(vm.nom, d.id); pushToast({ kind: "success", title: t("hd.removed"), message: d.produit || d.id }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("hd.removeFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <Card title={t("hd.title")} note={list ? list.length : null} flush actions={<button type="button" className="nx-btn nx-btn--sm" onClick={() => setAdding(true)}><Plus size={14} aria-hidden="true" />{t("hd.add")}</button>}>
      {error && list == null ? <ErrorState message={error} onRetry={reload} /> : list == null ? <Loading style={{ padding: "0 var(--space-4) var(--space-4)", margin: 0 }} /> : list.length === 0 ? <p className="nx-muted" style={{ padding: "0 var(--space-4) var(--space-4)", margin: 0 }}>{t("hd.none")}</p> : (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("hd.device")}</th><th scope="col">{t("hd.type")}</th><th scope="col">{t("hd.address")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {list.map((d) => (
                <tr key={d.id}>
                  <th scope="row" style={{ fontWeight: 500 }}>{d.produit || d.id}{d.fabricant && <span className="nx-muted"> · {d.fabricant}</span>}{!d.present && <> <Chip tone="warning">{t("hd.absent")}</Chip></>}</th>
                  <td><Chip>{d.type.toUpperCase()}</Chip></td>
                  <td className="nx-mono">{d.adresse || "—"}</td>
                  <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy || (d.type === "pci" && running)} title={d.type === "pci" && running ? t("hd.stopFirst") : undefined} aria-label={t("hd.removeAria", { name: d.produit || d.id })} onClick={() => remove(d)}>{t("hd.remove")}</button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      <p className="nx-f-h" style={{ margin: "var(--space-2) var(--space-4) var(--space-4)" }}>{t("hd.help")}</p>
      <AddHostDeviceDrawer open={adding} onClose={() => setAdding(false)} vm={vm} onDone={reload} />
    </Card>
  );
}

function AddHostDeviceDrawer({ open, onClose, vm, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [inv, setInv] = useState(null);
  const [error, setError] = useState(null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const running = vm.etat === "actif";
  useEffect(() => {
    if (!open) return;
    setPick(""); setInv(null);
    fetchHostDevices().then((r) => { setInv(r); setError(null); }).catch((e) => setError(errorMessage(e)));
  }, [open]);
  const all = inv ? [...inv.pci, ...inv.usb] : [];
  const byId = Object.fromEntries(all.map((d) => [d.id, d]));
  // Why a device cannot be chosen, or null.
  const blocked = (d) => (d.hote ? t("hd.hostUses", { reason: d.hote })
    : d.vm ? (d.vm === vm.nom ? t("hd.alreadyHere") : t("hd.givenTo", { vm: d.vm }))
    : d.type === "pci" && !inv.iommu.actif ? t("hd.noIommu")
    : d.type === "pci" && running ? t("hd.stopFirst") : null);
  const chosen = byId[pick];
  const group = chosen?.type === "pci" ? (chosen.groupe || []).map((id) => byId[id]).filter((m) => m && m.id !== chosen.id && !(m.classe || "").startsWith("0x06")) : [];
  async function add() {
    if (!chosen) return;
    const pci = chosen.type === "pci";
    if (pci && !(await confirmAction({ title: t("hd.confirmTitle", { name: chosen.produit }), message: t("hd.confirmMsg"), confirmLabel: t("hd.give"), danger: true }))) return;
    setBusy(true);
    try {
      await attachVMHostDevice(vm.nom, chosen.id, pci);
      pushToast({ kind: "success", title: t("hd.added"), message: chosen.produit });
      onDone(); onClose();
    } catch (e) { pushToast({ kind: "error", title: t("hd.addFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  const tile = (d) => {
    const why = blocked(d);
    return (
      <label key={d.id} className={`nx-tile nx-tile--radio${pick === d.id ? " is-on" : ""}`} aria-disabled={why ? true : undefined} style={why ? { opacity: 0.6 } : undefined}>
        <input type="radio" className="nx-tile-input" name="hostdev" checked={pick === d.id} disabled={Boolean(why)} onChange={() => setPick(d.id)} />
        <b>{d.produit}</b><small className="nx-mono">{[d.adresse, d.ids, d.pilote].filter(Boolean).join(" · ")}</small>
        <small>{d.fabricant}</small>{why && <small>{why}</small>}
      </label>
    );
  };
  return (
    <SideDrawer open={open} title={t("hd.add")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !chosen} onClick={add}>{t("hd.give")}</button>
    </>}>
      {error ? <ErrorState message={error} /> : !inv ? <Loading /> : (
        <>
          {!inv.iommu.actif && <p className="nx-notice nx-notice--warning" role="note">{t("hd.iommuOff")} {inv.iommu.raison}</p>}
          <fieldset className="nx-fieldset"><legend>USB</legend>
            {inv.usb.length === 0 ? <p className="nx-muted">{t("hd.noUsb")}</p> : <div className="nx-tiles">{inv.usb.map(tile)}</div>}
          </fieldset>
          <fieldset className="nx-fieldset"><legend>PCI</legend>
            {inv.pci.length === 0 ? <p className="nx-muted">{t("hd.noPci")}</p> : <div className="nx-tiles">{inv.pci.map(tile)}</div>}
          </fieldset>
          {group.length > 0 && <p className="nx-hint" role="note">{t("hd.group", { list: group.map((m) => `${m.adresse} ${m.produit}`).join(", ") })}</p>}
        </>
      )}
    </SideDrawer>
  );
}

// Hardware: the editable processor and memory, the disks (table, add from a drawer), the Windows drivers drive.
export function VmHardwarePage({ resource: vm }) {
  const t = useT();
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const pushToast = useInfraStore((s) => s.pushToast);
  const [disks, setDisks] = useState(null);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [resizing, setResizing] = useState(null);
  const [moving, setMoving] = useState(null);
  const [busy, setBusy] = useState(false);
  const name = vm?.nom;
  const node = vm?.node;
  const reload = useCallback(async () => {
    try { const d = await fetchVMDisks(name, node); setDisks(Array.isArray(d) ? d : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, [name, node]);
  useEffect(() => { if (name) reload(); }, [name, reload]);
  if (!vm) return null;

  async function detach(d) {
    if (!(await confirmAction({ title: t("vh.detachTitle", { dev: d.cible }), message: t("vh.detachMsg"), confirmLabel: t("vh.detach"), danger: true }))) return;
    setBusy(true);
    try { await detachDisk(vm.nom, d.cible); pushToast({ kind: "success", title: t("vh.detached"), message: d.cible }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("vh.detachFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  const size = (d) => (d.taille_go ? formatSizeGb(d.taille_go, lang()) : "—");
  return (
    <>
      <ComputeCard vm={vm} admin={admin} />
      <Card title={t("vh.disks")} note={disks ? disks.length : null} flush actions={admin && <button type="button" className="nx-btn nx-btn--sm" onClick={() => setAdding(true)}><Plus size={14} aria-hidden="true" />{t("vh.addDisk")}</button>}>
        {error && disks == null ? <ErrorState message={error} onRetry={reload} /> : disks == null ? <Loading style={{ padding: "0 var(--space-4) var(--space-4)", margin: 0 }} /> : disks.length === 0 ? <p className="nx-muted" style={{ padding: "0 var(--space-4) var(--space-4)", margin: 0 }}>{t("vh.noDisks")}</p> : (
          <TableWrap>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("vh.device")}</th><th scope="col">Bus</th><th scope="col">{t("vh.source")}</th><th scope="col" className="nx-num">{t("lib.size")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {disks.map((d) => (
                  <tr key={d.cible}>
                    <th scope="row" className="nx-mono" style={{ fontWeight: 500 }}>{d.cible}</th>
                    <td><Chip>{[d.bus ? d.bus.toUpperCase() : null, d.type === "cdrom" ? "CD" : null].filter(Boolean).join(" · ") || "—"}</Chip></td>
                    <td className="nx-mono nx-wrapcell">{d.source || <span className="nx-muted">{t("vh.emptyDrive")}</span>}{d.pool && <span className="nx-muted"> ({d.pool})</span>}</td>
                    <td className="nx-num nx-mono">{size(d)}</td>
                    <td><div className="nx-ra">{admin && d.type !== "cdrom" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy || !!d.non_agrandissable} title={d.non_agrandissable ? t(d.non_agrandissable === "iscsi" ? "vh.resizeIscsi" : "vh.resizeUnsupported") : undefined} aria-label={t("a11y.resize_disk_x", { v: d.cible })} onClick={() => setResizing(d)}>{t("vh.resize")}</button>}{admin && d.type !== "cdrom" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy || !d.pool || (d.source || "").startsWith("/dev/")} title={!d.pool || (d.source || "").startsWith("/dev/") ? t("vh.moveUnsupported") : undefined} aria-label={t("a11y.move_disk_x", { v: d.cible })} onClick={() => setMoving(d)}>{t("vh.move")}</button>}{admin && d.type !== "cdrom" && d.cible !== "vda" && d.cible !== "sda" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy} aria-label={t("a11y.detach_disk_x", { v: d.cible })} onClick={() => detach(d)}>{t("vh.detach")}</button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {admin && <DriversCard vmName={vm.nom} onChanged={reload} />}
      {admin && <HostDevicesCard vm={vm} />}
      <AddDiskDrawer open={adding} onClose={() => setAdding(false)} vmName={vm.nom} disks={disks} onDone={reload} />
      <ResizeDiskDrawer disk={resizing} onClose={() => setResizing(null)} vm={vm} onDone={reload} />
      <MoveDiskDrawer disk={moving} onClose={() => setMoving(null)} vm={vm} onDone={reload} />
    </>
  );
}

function AddInterfaceDrawer({ open, onClose, vmName, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [networks, setNetworks] = useState([]);
  const [net, setNet] = useState("");
  const [vlan, setVlan] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) fetchNetworks().then((n) => { const l = Array.isArray(n) ? n : []; setNetworks(l); setNet((p) => p || l[0]?.nom || ""); }).catch(() => setNetworks([])); }, [open]);
  const vlanBad = vlan !== "" && (!Number.isInteger(Number(vlan)) || Number(vlan) < 1 || Number(vlan) > 4094);
  async function add() {
    if (!net || vlanBad) return;
    setBusy(true);
    try { await attachInterface(vmName, net, vlan ? Number(vlan) : null); pushToast({ kind: "success", title: t("vh.ifAdded"), message: net }); setVlan(""); onDone(); onClose(); }
    catch (er) { pushToast({ kind: "error", title: t("sec.addFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }
  return (
    <SideDrawer open={open} title={t("vh.addIf")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !net || vlanBad} onClick={add}>{t("vh.addIfBtn")}</button>
    </>}>
      <Field label={t("vh.network")}>{(p) => <select {...p} className="nx-inp" aria-label={t("a11y.network_to_attach")} value={net} onChange={(e) => setNet(e.target.value)}>{networks.map((n) => <option key={n.nom} value={n.nom}>{n.nom} ({t(`net.mode.${n.type}`)})</option>)}</select>}</Field>
      <Field label="VLAN" error={vlanBad ? t("vh.vlanRule") : null} hint={t("vh.vlanHint")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.vlan_optional")} type="number" min={1} max={4094} value={vlan} placeholder={t("vh.optional")} onChange={(e) => setVlan(e.target.value)} />}</Field>
    </SideDrawer>
  );
}

// Network: interfaces (network, model, MAC, VLAN, firewall) and the VM firewall rules.
export function VmNetworkPage({ resource: vm }) {
  const t = useT();
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const [info, setInfo] = useState(null);
  const [nets, setNets] = useState([]);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const name = vm?.nom;
  const node = vm?.node;
  const reload = useCallback(async () => {
    try { const [i, n] = await Promise.all([fetchVMNetwork(name, node), fetchNetworks().catch(() => [])]); setInfo(i); setNets(Array.isArray(n) ? n : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, [name, node]);
  useEffect(() => { if (name) reload(); }, [name, reload]);
  // The VM list in the browser can still hold a VM that was just deleted (it is refreshed every few seconds):
  // on "not found", refresh it now so the page shows the VM as missing instead of a stale one.
  const fetchFw = useCallback(() => fetchVMFirewall(name).catch((e) => { if (e.status === 404) refreshAll?.(); throw e; }), [name, refreshAll]);
  const saveFw = useCallback((c) => setVMFirewall(name, c), [name]);
  if (!vm) return null;
  const ifaces = info?.interfaces || [];
  const netOf = (n) => nets.find((x) => x.nom === n);

  async function detach(i) {
    if (!(await confirmAction({ title: t("vh.ifDetachTitle", { mac: i.mac }), message: t("vh.ifDetachMsg"), confirmLabel: t("sec.remove"), danger: true }))) return;
    setBusy(true);
    try { await detachInterface(vm.nom, i.mac); pushToast({ kind: "success", title: t("vh.ifDetached"), message: i.mac }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("vh.detachFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  return (
    <>
      <Card title={t("vh.interfaces")} note={info ? ifaces.length : null} flush actions={admin && <button type="button" className="nx-btn nx-btn--sm" onClick={() => setAdding(true)}><Plus size={14} aria-hidden="true" />{t("vh.addIf")}</button>}>
        {error && !info ? <ErrorState message={error} onRetry={reload} /> : !info ? <Loading style={{ padding: "0 var(--space-4) var(--space-4)", margin: 0 }} /> : (
          <TableWrap>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("vh.network")}</th><th scope="col">{t("vh.model")}</th><th scope="col">MAC</th><th scope="col">VLAN</th><th scope="col">{t("vh.firewallCol")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {ifaces.map((i) => {
                  const n = netOf(i.reseau);
                  return (
                    <tr key={i.mac}>
                      <th scope="row" className="nx-nm">{i.reseau || <span className="nx-muted">{i.type_source || t("ns.notReported")}</span>}{n && <small>{[t(`net.mode.${n.type}`), n.reseau?.adresse].filter(Boolean).join(" · ")}</small>}</th>
                      <td>{i.modele ? <Chip>{i.modele}</Chip> : <span className="nx-muted">—</span>}</td>
                      <td className="nx-mono">{i.mac}</td>
                      <td className="nx-mono">{i.vlan ?? <span className="nx-muted">—</span>}</td>
                      <td>{i.pare_feu ? <span className="nx-st nx-tone-success"><span className="nx-dot" data-tone="success" aria-hidden="true" />{t("state.active")}</span> : <span className="nx-muted">{t("vh.noFirewall")}</span>}</td>
                      <td><div className="nx-ra">{admin && ifaces.length > 1 && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" disabled={busy} aria-label={t("a11y.remove_interface_x", { v: i.mac })} title={t("sec.remove")} onClick={() => detach(i)}><Trash2 size={15} aria-hidden="true" /></button>}</div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <FirewallCard title={t("vh.firewall")} fetchConfig={fetchFw} saveConfig={saveFw} isAdmin={admin} />
      <AddInterfaceDrawer open={adding} onClose={() => setAdding(false)} vmName={vm.nom} onDone={reload} />
    </>
  );
}

// ---- Options and limits: cgroup limits applied live -------------------------------------------------------
export function VmOptionsPage({ resource: vm }) {
  const t = useT();
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const pushToast = useInfraStore((s) => s.pushToast);
  const [limits, setLimits] = useState(null);
  const [error, setError] = useState(null);
  const [shares, setShares] = useState("1024");
  const [cpu, setCpu] = useState("");
  const [ram, setRam] = useState("");
  const [busy, setBusy] = useState(false);
  const name = vm?.nom;
  const load = useCallback(async () => {
    try { const l = await fetchVMLimits(name); setLimits(l); setShares(String(l.cpu_shares)); setCpu(l.cpu_limit_pct ?? ""); setRam(l.mem_hard_limit_mb ?? ""); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, [name]);
  useEffect(() => { if (name) load(); }, [name, load]);
  if (!vm) return null;
  if (error && !limits) return <ErrorState message={error} onRetry={load} />;
  if (!limits) return <Loading />;
  const bad = { shares: !intIn(shares, 2, 262144), cpu: cpu !== "" && !intIn(cpu, 1, 100), ram: ram !== "" && !intIn(ram, 64) };
  const dirty = Number(shares) !== limits.cpu_shares || (cpu === "" ? null : Number(cpu)) !== (limits.cpu_limit_pct ?? null) || (ram === "" ? null : Number(ram)) !== (limits.mem_hard_limit_mb ?? null);
  async function save() {
    if (bad.shares || bad.cpu || bad.ram) return;
    setBusy(true);
    try {
      const u = await setVMLimits(vm.nom, { cpu_shares: Number(shares), cpu_limit_pct: cpu === "" ? null : Number(cpu), mem_hard_limit_mb: ram === "" ? null : Number(ram) });
      setLimits(u); pushToast({ kind: "success", title: t("vo.applied"), message: vm.nom });
    } catch (er) { pushToast({ kind: "error", title: t("vo.applyFailed"), message: errorMessage(er) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("vo.limits")}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-4)", fontSize: "var(--fs-13)" }}>{t("vo.limitsHelp")}</p>
      <div className="nx-fg nx-fg--3">
        <Field label={t("vo.shares")} unit={t("vo.parts")} error={bad.shares ? t("vo.sharesRule") : null} hint={t("vo.sharesHelp")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.cpu_shares")} type="number" min={2} max={262144} disabled={!admin} value={shares} onChange={(e) => setShares(e.target.value)} />}</Field>
        <Field label={t("vo.cpuCap")} unit="% / vCPU" error={bad.cpu ? t("vo.cpuCapRule") : null} hint={t("vo.cpuCapHelp")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.max_cpu_limit_percent_per_vcpu")} type="number" min={1} max={100} placeholder={t("vo.unlimited")} disabled={!admin} value={cpu} onChange={(e) => setCpu(e.target.value)} />}</Field>
        <Field label={t("vo.ramCap")} unit={lang() === "fr" ? "Mo" : "MB"} error={bad.ram ? t("vo.ramCapRule") : null} hint={t("vo.ramCapHelp")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.ram_limit_in_mb")} type="number" min={64} placeholder={t("vo.unlimited")} disabled={!admin} value={ram} onChange={(e) => setRam(e.target.value)} />}</Field>
      </div>
      {admin && <div className="nx-fa"><button type="button" className="nx-btn" aria-label={t("a11y.apply_live")} disabled={!dirty || busy || bad.shares || bad.cpu || bad.ram} onClick={save}><Zap size={14} aria-hidden="true" />{t("vo.applyLive")}</button></div>}
    </Card>
  );
}
