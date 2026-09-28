import { useEffect, useMemo, useRef, useState } from "react";
import { createVM, fetchClusterIsos, fetchVmDisks, fetchVolumes } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { installationFamily, isWindowsInstall, guestProfile, diskController } from "../../utils/osFamily";
import { useHostLimits } from "../../hooks/useHostLimits";
import OverallocationNote from "../../components/OverallocationNote";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";
import VmDiskUploadDropzone from "../../components/VmDiskUploadDropzone";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatSizeGb, formatSizeMb } from "../lib/format";

const STEPS = ["source", "identity", "placement", "compute", "storage", "network", "advanced", "review"];
const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;
const int = (v, min, max) => v !== "" && v != null && Number.isInteger(Number(v)) && Number(v) >= min && (max == null || Number(v) <= max);

// The sizes the form starts from (a Windows installation raises them, see patch()).
const DEFAULTS = { vcpu: 2, memory_mb: 2048, disk_gb: 20 };

function initialForm(nodes, networks, d = DEFAULTS) {
  return {
    node: (nodes.find((n) => !n.maintenance) || nodes[0])?.id || "", iso: "", isoNode: "local", driversIso: "", driversIsoNode: "local", guestOs: "auto", diskController: "auto", importDisk: null,
    name: "", vcpu: d.vcpu, memory_mb: d.memory_mb, disks: [{ size_gb: d.disk_gb }],
    username: "", password: "", network: networks[0]?.nom || "default", storagePool: "", eraseLuns: false, autoCleanupEnabled: false, autoCleanupDays: 7,
  };
}

// Creation wizard in eight steps (Source, Identity, Placement, Compute, Storage, Network, Advanced, Review).
// Same POST /vms payload as the historical wizard. "Next" is always available: an invalid step shows its
// errors instead of advancing, so nothing is silently blocked. Creation is not idempotent: one submit only,
// and a refusal keeps the dialog and every typed value.
export default function CreateVmWizard({ open, onClose, triggerRef }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const nodes = useInfraStore((s) => s.nodes);
  const networks = useInfraStore((s) => s.networks);
  const pools = useInfraStore((s) => s.storagePools);
  const vms = useInfraStore((s) => s.vms);
  const addTask = useInfraStore((s) => s.addTask);
  const completeTask = useInfraStore((s) => s.completeTask);
  const loadAll = useInfraStore((s) => s.loadAll);
  const pushToast = useInfraStore((s) => s.pushToast);
  const limits = useHostLimits();

  const [step, setStep] = useState(0);
  const [form, setForm] = useState(() => initialForm(nodes, networks));
  const [isos, setIsos] = useState([]);
  const [disks, setDisks] = useState([]);
  const [luns, setLuns] = useState(null); // LUNs of the chosen iSCSI pool, null while unknown
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const bodyRef = useRef(null);

  // Images of every node: this host's first, then those only another node has (copied here before creation, since
  // a VM boots only from an image on its own host). One entry per name.
  const reloadIsos = () => fetchClusterIsos().then((r) => {
    const all = Array.isArray(r?.isos) ? r.isos : [];
    const local = all.filter((i) => i.node === "local");
    const here = new Set(local.map((i) => i.nom));
    const remote = [];
    for (const i of all) if (i.node !== "local" && !here.has(i.nom) && !remote.some((x) => x.nom === i.nom)) remote.push(i);
    setIsos([...local, ...remote.sort((a, b) => a.nom.localeCompare(b.nom))]);
  }).catch(() => {});
  const isoNodeName = (id) => nodes.find((n) => n.id === id)?.nom || id;
  const isoSub = (iso) => (iso.node === "local" ? `${formatSizeMb(iso.taille_mo, lang)} · ISO`
    : t("wz.src.remoteIso", { size: formatSizeMb(iso.taille_mo, lang), node: isoNodeName(iso.node) }));
  const reloadDisks = () => fetchVmDisks().then((r) => setDisks(Array.isArray(r) ? r : [])).catch(() => {});
  useEffect(() => {
    if (open) { reloadIsos(); reloadDisks(); }
  }, [open]);
  useEffect(() => { bodyRef.current?.scrollTo?.(0, 0); }, [step]);

  function patch(fields) {
    setForm((f) => {
      const next = { ...f, ...fields };
      if (isWindowsInstall(next) && !isWindowsInstall(f)) {
        return { ...next, driversIso: "", vcpu: Math.max(next.vcpu, 2), memory_mb: Math.max(next.memory_mb, 4096), disks: next.disks.map((d, i) => (i === 0 ? { ...d, size_gb: Math.max(d.size_gb, 64) } : d)) };
      }
      return next;
    });
  }
  useEffect(() => { // import mode: preselect the first free disk
    if (form.importDisk != null && (!form.importDisk || form.importDisk === "__pending__") && disks.length > 0) patch({ importDisk: disks[0].nom });
  }, [form.importDisk, disks]);

  // The inventory may arrive after the dialog is created: fill the node and network as soon as they exist.
  useEffect(() => {
    setForm((f) => {
      const node = f.node || nodes[0]?.id || "";
      const network = networks.some((n) => n.nom === f.network) ? f.network : networks[0]?.nom || f.network;
      return node === f.node && network === f.network ? f : { ...f, node, network };
    });
  }, [nodes, networks]);

  const importMode = form.importDisk != null;
  const manual = Boolean(form.iso) && !installationFamily(form);
  const needsAccount = !importMode && !manual;
  const taken = useMemo(() => new Set(vms.map((v) => v.nom)), [vms]);
  const vMin = limits?.vcpu.min ?? 1, vMax = limits?.vcpu.max, mMin = limits?.memoire_mo.min ?? 256, mMax = limits?.memoire_mo.max, dMax = limits?.disque_go.max, dCount = limits?.disques.max;

  // iSCSI pool: each disk takes a whole free LUN (created on the storage side), and the chosen LUNs are erased.
  const iscsiPool = pools.find((p) => p.nom === form.storagePool && p.type === "iscsi") || null;
  useEffect(() => {
    if (!iscsiPool) { setLuns(null); return undefined; }
    let alive = true;
    setLuns(null);
    fetchVolumes(iscsiPool.nom).then((v) => { if (alive) setLuns(Array.isArray(v) ? v : []); }).catch(() => { if (alive) setLuns([]); });
    return () => { alive = false; };
  }, [iscsiPool?.nom]); // eslint-disable-line react-hooks/exhaustive-deps
  const lunGb = (nom) => Math.max(1, Math.ceil(luns?.find((l) => l.nom === nom)?.capacite_go || 1));
  const freeLuns = (luns || []).filter((l) => !l.utilise);

  // Every rule returns an i18n key (or "") so the messages follow the language.
  const iscsiErrors = iscsiPool ? {
    ...Object.fromEntries(form.disks.map((d, i) => [`disk${i}`, d.lun ? "" : "wz.e.lun"]).filter(([, v]) => v)),
    ...(form.eraseLuns ? {} : { erase: "wz.e.erase" }),
  } : null;
  const errors = {
    source: importMode && (!form.importDisk || form.importDisk === "__pending__") ? { importDisk: "wz.e.disk" } : {},
    identity: {
      ...(!NAME_RE.test(form.name) ? { name: "wz.e.name" } : taken.has(form.name) ? { name: "wz.e.taken" } : {}),
      ...(needsAccount && !/^[a-zA-Z_][a-zA-Z0-9_-]{0,31}$/.test(form.username) ? { username: "wz.e.user" } : {}),
      ...(needsAccount && form.password.length < 4 ? { password: "wz.e.password" } : {}),
    },
    // Creation always runs on this host for now (see wz.nodeWarn), so this host in maintenance refuses it whatever
    // tile is picked: say so here rather than after the eight steps.
    placement: !form.node ? { node: "wz.e.node" } : nodes.find((n) => n.id === form.node)?.maintenance || nodes.find((n) => n.id === "local")?.maintenance ? { node: "wz.e.nodeMaint" } : {},
    compute: { ...(!int(form.vcpu, vMin, vMax) ? { vcpu: "wz.e.vcpu" } : {}), ...(!int(form.memory_mb, mMin, mMax) ? { memory_mb: "wz.e.memory" } : {}) },
    storage: iscsiErrors || Object.fromEntries(form.disks.map((d, i) => [`disk${i}`, importMode && i === 0 ? "" : int(d.size_gb, 1, dMax) ? "" : "wz.e.diskSize"]).filter(([, v]) => v)),
    network: networks.some((n) => n.nom === form.network) ? {} : { network: "wz.e.network" },
    advanced: form.autoCleanupEnabled && !int(form.autoCleanupDays, 1, 365) ? { days: "wz.e.days" } : {},
    review: {},
  };
  const stepId = STEPS[step];
  const valid = (id) => Object.keys(errors[id]).length === 0;
  const allValid = STEPS.every(valid);
  const show = (id, k) => attempted && errors[id][k] && <span className="nx-hint nx-hint--error" id={`wz-${k}-err`}>{t(errors[id][k], { min: k === "vcpu" ? vMin : mMin, max: k === "vcpu" ? vMax ?? "∞" : k === "memory_mb" ? mMax ?? "∞" : dMax ?? "∞" })}</span>;
  const inv = (id, k) => ({ "aria-invalid": attempted && errors[id][k] ? true : undefined, "aria-describedby": attempted && errors[id][k] ? `wz-${k}-err` : undefined });

  const dirty = Boolean(form.name || form.username || form.password || form.iso || (form.importDisk && form.importDisk !== "__pending__"));
  function reset() { setStep(0); setForm(initialForm(nodes, networks)); setAttempted(false); setError(null); }
  async function requestClose() {
    if (busy) return;
    if (dirty && !(await confirmAction({ title: t("wz.discardTitle"), message: t("wz.discardMsg"), confirmLabel: t("wz.discard") }))) return;
    onClose(); reset();
  }
  function next() {
    if (!valid(stepId)) { setAttempted(true); return; }
    setAttempted(false); setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }
  function goTo(i) { if (i <= step || STEPS.slice(0, i).every(valid)) { setAttempted(false); setStep(i); } }

  async function create() {
    if (busy) return;
    const firstBad = STEPS.findIndex((id) => !valid(id));
    if (firstBad !== -1) { setAttempted(true); setStep(firstBad); return; }
    setBusy(true); setError(null);
    const payload = {
      name: form.name, vcpu: Number(form.vcpu), memory_mb: Number(form.memory_mb), disks: form.disks.map((d) => (iscsiPool ? { size_gb: lunGb(d.lun), lun: d.lun } : { size_gb: Number(d.size_gb) })), network: form.network,
      username: form.username, password: form.password, iso: form.iso || null,
      drivers_iso: form.iso && !importMode ? form.driversIso || null : null,
      iso_node: form.iso ? form.isoNode : null, drivers_iso_node: form.iso && !importMode && form.driversIso ? form.driversIsoNode : null, guest_os: form.guestOs, disk_controller: form.diskController,
      import_disk: importMode && form.importDisk !== "__pending__" ? form.importDisk : null, storage_pool: form.storagePool || null,
      auto_cleanup_days: form.autoCleanupEnabled ? Number(form.autoCleanupDays) : null,
      erase_luns: Boolean(iscsiPool && form.eraseLuns),
    };
    const copied = [payload.iso_node, payload.drivers_iso_node].some((n) => n && n !== "local");
    if (copied) {
      // The server copies the image(s) here, then creates the VM: both run as its own tasks, even if this page closes.
      try {
        await createVM(payload);
        pushToast({ kind: "success", title: t("wz.copyThenCreate"), message: t("wz.copyThenCreateMsg", { name: form.name }) });
        await loadAll(); onClose(); reset();
      } catch (e) { setError(errorMessage(e)); }
      finally { setBusy(false); }
      return;
    }
    const taskId = addTask({ type: "create_vm", cible: form.name, node: form.node });
    try { await createVM(payload); completeTask(taskId, "termine"); await loadAll(); onClose(); reset(); }
    catch (e) { completeTask(taskId, "echec", errorMessage(e)); setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  const selectable = pools.filter((p) => ["dir", "netfs", "zfs", "iscsi"].includes(p.type) && p.etat === "actif");
  const family = installationFamily(form);
  const profile = guestProfile(form);
  const radio = (checked, on, title, sub, name, extra = null, disabled = false) => (
    <label key={`${name}-${title}`} className={`nx-tile nx-tile--radio${checked ? " is-on" : ""}`} aria-disabled={disabled || undefined} style={disabled ? { opacity: 0.55 } : undefined}><input type="radio" className="nx-tile-input" name={name} checked={checked} onChange={on} disabled={disabled} /><b>{title}</b>{sub && <small>{sub}</small>}{extra}</label>
  );
  const nodeName = nodes.find((n) => n.id === form.node)?.nom || form.node;

  const sourceNote = importMode ? t("wz.src.import") : !form.iso ? t("wz.src.cloud") : isWindowsInstall(form) ? t("wz.src.windows") : family === "kickstart" ? t("wz.src.kickstart") : family === "autoinstall" ? t("wz.src.autoinstall") : t("wz.src.manual");

  const review = [
    [0, t("wz.step.source"), [[t("wz.r.source"), importMode ? `${t("wz.r.imported")} (${form.importDisk || "—"})` : form.iso || t("wz.r.debian")], [t("wz.r.profile"), importMode ? "—" : { windows: "Windows", linux: "Linux", other: t("wz.os.other") }[profile]]]],
    [1, t("wz.step.identity"), [[t("ct.name"), form.name || "—"], [t("sec.username"), needsAccount ? form.username : importMode ? t("wz.r.onDisk") : t("wz.r.duringInstall")]]],
    [2, t("wz.step.placement"), [[t("ns.node"), nodeName], [t("stor.pool"), form.storagePool || t("wz.r.defaultPool")]]],
    [3, t("wz.step.compute"), [["vCPU", form.vcpu], [t("ct.memory"), formatSizeMb(Number(form.memory_mb), lang)]]],
    [4, t("wz.step.storage"), [[t("vh.disks"), form.disks.map((d, i) => (iscsiPool ? d.lun || "—" : importMode && i === 0 ? t("wz.r.imported") : `${d.size_gb} GB`)).join(" + ")], [t("wz.controller"), diskController(form) === "sata" ? "SATA" : "VirtIO SCSI"]]],
    [5, t("wz.step.network"), [[t("vh.network"), form.network], [t("wz.adapter"), profile === "linux" ? "VirtIO" : "Intel E1000e"]]],
    [6, t("wz.step.advanced"), [[t("wz.drivers"), form.driversIso || t("wz.none")], [t("wz.cleanup"), form.autoCleanupEnabled ? t("wz.r.cleanupDays", { n: form.autoCleanupDays }) : t("wz.off")]]],
  ];

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) requestClose(); }}>
      <DialogContent className="nx-wizard nx-wizard2 w-full max-w-5xl sm:max-w-5xl p-0 gap-0 overflow-hidden" onCloseAutoFocus={(e) => { if (triggerRef?.current) { e.preventDefault(); triggerRef.current.focus(); } }}>
        <DialogHeader className="nx-wiz-head">
          <DialogTitle>{t("wz.title")}</DialogTitle>
          <DialogDescription className="nx-sr">{t("wz.desc")}</DialogDescription>
        </DialogHeader>
        <div className="nx-wiz3">
        <ol className="nx-steps2" aria-label={t("wz.steps")}>
          {STEPS.map((id, i) => (
            <li key={id} aria-current={i === step ? "step" : undefined} className={i < step ? "is-done" : i === step ? "is-current" : ""}>
              <button type="button" disabled={i > step && !STEPS.slice(0, i).every(valid)} onClick={() => goTo(i)}>
                <span className="nx-stepnum" aria-hidden="true">{i < step ? "✓" : i + 1}</span><span className="nx-steplabel">{t(`wz.step.${id}`)}</span>
              </button>
            </li>
          ))}
        </ol>

        <div className="nx-wiz-body" ref={bodyRef}>
          <h3 className="nx-wiz-title">{t(`wz.step.${stepId}`)}</h3>
          <p className="nx-wiz-lead">{t(`wz.q.${stepId}`)}</p>
          {stepId === "source" && (
            <div className="nx-form">
              <div className="nx-tiles" role="group" aria-label={t("wz.sourceType")}>
                <button type="button" className="nx-tile" aria-pressed={!importMode} onClick={() => patch({ importDisk: null })}><b>{t("wz.src.image")}</b><small>{t("wz.src.imageSub")}</small></button>
                <button type="button" className="nx-tile" aria-pressed={importMode} onClick={() => patch({ iso: "", importDisk: disks[0]?.nom || "__pending__" })}><b>{t("wz.src.importBtn")}</b><small>{t("wz.src.importSub")}</small></button>
              </div>
              <p className="nx-notice" role="note">{sourceNote}</p>
              {isWindowsInstall(form) && <p className="nx-hint">{t("wz.src.windowsNote")}</p>}
              {importMode ? (
                <fieldset className="nx-fieldset">
                  <legend>{t("wz.src.disk")}</legend>
                  {disks.length === 0 && <span className="nx-muted">{t("wz.src.noDisks")}</span>}
                  <div className="nx-tiles">{disks.map((d) => radio(form.importDisk === d.nom, () => patch({ importDisk: d.nom }), d.nom, formatSizeMb(d.taille_mo, lang), "import"))}</div>
                  {show("source", "importDisk")}
                  <VmDiskUploadDropzone onDone={reloadDisks} labels={{ drop: t("up.dropDisk"), done: t("up.done"), eta: t("up.eta") }} />
                </fieldset>
              ) : (
                <>
                  <fieldset className="nx-fieldset">
                    <legend>{t("wz.src.iso")}</legend>
                    <div className="nx-tiles">
                      {radio(!form.iso, () => patch({ iso: "" }), t("wz.src.debian"), t("wz.src.debianSub"), "iso")}
                      {isos.map((iso) => radio(form.iso === iso.nom, () => patch({ iso: iso.nom, isoNode: iso.node }), iso.nom, isoSub(iso), "iso"))}
                    </div>
                  </fieldset>
                  {form.iso && (
                    <label>{t("wz.os")}
                      <select className="nx-input" aria-label={t("a11y.operating_system")} value={form.guestOs || "auto"} onChange={(e) => patch({ guestOs: e.target.value })}>
                        <option value="auto">{t("wz.os.auto")}</option><option value="windows">Windows / Windows Server</option><option value="linux">Linux (VirtIO)</option><option value="other">{t("wz.os.other")}</option>
                      </select>
                      <span className="nx-hint">{t("wz.os.help")}</span>
                    </label>
                  )}
                </>
              )}
            </div>
          )}

          {stepId === "identity" && (
            <div className="nx-form">
              <label>{t("wz.vmName")}<input className="nx-input" aria-label={t("a11y.vm_name")} autoFocus value={form.name} onChange={(e) => patch({ name: e.target.value })} placeholder="web-03" {...inv("identity", "name")} />{show("identity", "name")}</label>
              {needsAccount ? (
                <div className="nx-formgrid">
                  <label>{t("ct.user")}<input className="nx-input" aria-label={t("a11y.user")} autoComplete="off" value={form.username} onChange={(e) => patch({ username: e.target.value })} placeholder="alice" {...inv("identity", "username")} />{show("identity", "username")}</label>
                  <label>{t("ct.password")}<input className="nx-input" aria-label={t("a11y.password")} type="password" autoComplete="new-password" value={form.password} onChange={(e) => patch({ password: e.target.value })} {...inv("identity", "password")} />{show("identity", "password")}</label>
                </div>
              ) : <p className="nx-notice" role="note">{importMode ? t("wz.acct.import") : t("wz.acct.manual")}</p>}
            </div>
          )}

          {stepId === "placement" && (
            <div className="nx-form">
              <fieldset className="nx-fieldset">
                <legend>{t("ns.node")}</legend>
                <div className="nx-tiles">{nodes.map((n) => {
                  const ram = n.memoire_totale_mo ? (n.memoire_utilisee_mo / n.memoire_totale_mo) * 100 : null;
                  return radio(form.node === n.id, () => patch({ node: n.id }), n.nom,
                    n.maintenance ? t("wz.inMaintenance") : [n.cpu_utilisation != null ? `CPU ${Math.round(n.cpu_utilisation)} %` : null, ram != null ? `RAM ${Math.round(ram)} %` : null, `${n.vms_actives ?? 0} ${t("wz.running")}`].filter(Boolean).join(" · "),
                    "node", ram != null && !n.maintenance ? <span className="nx-track nx-track--wide" style={{ marginTop: "var(--space-2)" }} aria-hidden="true"><span data-tone={ram >= 90 ? "danger" : ram >= 80 ? "warning" : "info"} style={{ width: `${Math.round(ram)}%` }} /></span> : null, Boolean(n.maintenance));
                })}</div>
                {show("placement", "node")}
                {form.node && form.node !== "local" && <p className="nx-notice nx-notice--warning" role="note">{t("wz.nodeWarn")}</p>}
              </fieldset>
              {selectable.length > 0 && (
                <label>{t("stor.pool")}
                  <select className="nx-input" aria-label={t("a11y.storage_pool")} value={form.storagePool} onChange={(e) => patch({ storagePool: e.target.value })}>
                    <option value="">{t("wz.r.defaultPool")}</option>
                    {selectable.map((p) => <option key={p.nom} value={p.nom}>{p.nom} ({p.type === "netfs" ? "NFS" : p.type === "iscsi" ? "iSCSI" : p.type}{p.type === "iscsi" ? "" : `, ${p.disponible_go} GB ${t("stor.free").toLowerCase()}`})</option>)}
                  </select>
                  <span className="nx-hint">{t("wz.poolHelp")}</span>
                </label>
              )}
            </div>
          )}

          {stepId === "compute" && (
            <div className="nx-form">
              <div className="nx-formgrid">
                <label>vCPU{limits ? ` (${vMin}–${vMax ?? "∞"})` : ""}<input className="nx-input" aria-label={t("a11y.vcpu")} type="number" min={vMin} max={vMax} value={form.vcpu} onChange={(e) => patch({ vcpu: e.target.value })} {...inv("compute", "vcpu")} />{show("compute", "vcpu")}</label>
                <label>{t("ct.memory")} (MB{limits ? `, ${mMin}–${mMax ?? "∞"}` : ""})<input className="nx-input" aria-label={t("a11y.memory_in_mb")} type="number" min={mMin} max={mMax} step={128} value={form.memory_mb} onChange={(e) => patch({ memory_mb: e.target.value })} {...inv("compute", "memory_mb")} />{show("compute", "memory_mb")}</label>
              </div>
              <OverallocationNote limits={limits} vcpu={Number(form.vcpu) || 0} memoryMb={Number(form.memory_mb) || 0} diskGb={Math.max(0, ...form.disks.map((d) => Number(d.size_gb) || 0))} />
            </div>
          )}

          {stepId === "storage" && iscsiPool && (
            <div className="nx-form">
              <fieldset className="nx-fieldset">
                <legend>{t("vh.disks")} · iSCSI {iscsiPool.nom}</legend>
                {luns == null ? <span className="nx-muted">{t("loading")}</span> : freeLuns.length === 0 ? <p className="nx-notice nx-notice--warning" role="note">{t("wz.noFreeLun")}</p> : null}
                {luns != null && form.disks.map((d, i) => (
                  <div key={i} className="nx-inline">
                    <span className="nx-mono" style={{ width: "2.5rem", alignSelf: "center" }}>sd{String.fromCharCode(97 + i)}</span>
                    <select className="nx-input" aria-label={t("wz.lunOf", { v: i + 1 })} value={d.lun || ""} onChange={(e) => patch({ disks: form.disks.map((x, k) => (k === i ? { ...x, lun: e.target.value } : x)) })} aria-invalid={attempted && errors.storage[`disk${i}`] ? true : undefined}>
                      <option value="">{t("wz.lunPick")}</option>
                      {freeLuns.filter((l) => l.nom === d.lun || !form.disks.some((x) => x.lun === l.nom)).map((l) => <option key={l.nom} value={l.nom}>{l.nom} · {formatSizeGb(l.capacite_go, lang)}</option>)}
                    </select>
                    <button type="button" className="nx-btn" aria-label={t("a11y.remove_disk_x", { v: i + 1 })} disabled={form.disks.length <= 1} onClick={() => patch({ disks: form.disks.filter((_, k) => k !== i) })}>{t("sec.remove")}</button>
                  </div>
                ))}
                {attempted && Object.keys(errors.storage).some((k) => k.startsWith("disk")) && <span className="nx-hint nx-hint--error">{t("wz.e.lun")}</span>}
                <div><button type="button" className="nx-btn" disabled={form.disks.length >= freeLuns.length} onClick={() => patch({ disks: [...form.disks, { size_gb: 1, lun: "" }] })}>{t("wz.addDisk")}</button></div>
              </fieldset>
              <label className="nx-check" style={{ display: "flex", alignItems: "flex-start", gap: "0.6rem" }}>
                <input type="checkbox" style={{ marginTop: "0.2rem" }} checked={form.eraseLuns} onChange={(e) => patch({ eraseLuns: e.target.checked })} aria-invalid={attempted && errors.storage.erase ? true : undefined} />
                <span><b>{t("wz.erase")}</b><br /><span className="nx-hint">{t("wz.eraseHelp")}</span></span>
              </label>
              {attempted && errors.storage.erase && <span className="nx-hint nx-hint--error">{t("wz.e.erase")}</span>}
            </div>
          )}

          {stepId === "storage" && !iscsiPool && (
            <div className="nx-form">
              <fieldset className="nx-fieldset">
                <legend>{t("vh.disks")} (GB)</legend>
                {form.disks.map((d, i) => (
                  <div key={i} className="nx-inline">
                    <span className="nx-mono" style={{ width: "2.5rem", alignSelf: "center" }}>sd{String.fromCharCode(97 + i)}</span>
                    {importMode && i === 0 ? <span className="nx-input nx-muted">{t("wz.importedSize")}</span> : (
                      <input className="nx-input" aria-label={t("a11y.size_of_disk_x_in_gb", { v: i + 1 })} type="number" min={1} max={dMax} value={d.size_gb} onChange={(e) => patch({ disks: form.disks.map((x, k) => (k === i ? { size_gb: e.target.value } : x)) })} aria-invalid={attempted && errors.storage[`disk${i}`] ? true : undefined} />
                    )}
                    <button type="button" className="nx-btn" aria-label={t("a11y.remove_disk_x", { v: i + 1 })} disabled={form.disks.length <= 1 || (importMode && i === 0)} onClick={() => patch({ disks: form.disks.filter((_, k) => k !== i) })}>{t("sec.remove")}</button>
                  </div>
                ))}
                {attempted && Object.values(errors.storage).length > 0 && <span className="nx-hint nx-hint--error">{t("wz.e.diskSize", { max: dMax ?? "∞" })}</span>}
                <div><button type="button" className="nx-btn" disabled={dCount != null && form.disks.length >= dCount} onClick={() => patch({ disks: [...form.disks, { size_gb: 5 }] })}>{t("wz.addDisk")}</button></div>
              </fieldset>
              <label>{t("wz.controller")}
                <select className="nx-input" aria-label={t("a11y.disk_controller")} value={form.diskController || "auto"} onChange={(e) => patch({ diskController: e.target.value })}>
                  <option value="auto">{t("wz.ctl.auto", { name: profile === "linux" ? "VirtIO SCSI" : "SATA" })}</option><option value="sata">{t("wz.ctl.sata")}</option><option value="virtio-scsi">{t("wz.ctl.virtio")}</option>
                </select>
                <span className="nx-hint">{diskController(form) === "sata" ? t("wz.ctl.sataHelp") : profile === "windows" ? t("wz.ctl.winHelp") : t("wz.ctl.virtioHelp")}{importMode && ` ${t("wz.ctl.importHelp")}`}</span>
              </label>
            </div>
          )}

          {stepId === "network" && (
            <fieldset className="nx-fieldset">
              <legend>{t("vh.network")}</legend>
              <div className="nx-tiles">{networks.map((n) => radio(form.network === n.nom, () => patch({ network: n.nom }), n.nom, `${t(`net.mode.${n.type}`)} · ${n.pont || "—"} · ${n.reseau ? n.reseau.adresse : t("nn.noSubnet")}`, "network"))}</div>
              {show("network", "network")}
            </fieldset>
          )}

          {stepId === "advanced" && (
            <div className="nx-form">
              {form.iso && !importMode && (
                <fieldset className="nx-fieldset">
                  <legend>{t("wz.drivers")}</legend>
                  <select className="nx-input" aria-label={t("a11y.drivers_iso")} value={form.driversIso || ""} onChange={(e) => patch({ driversIso: e.target.value, driversIsoNode: isos.find((i) => i.nom === e.target.value)?.node || "local" })}>
                    <option value="">{t("wz.none")}</option>{isos.filter((i) => i.nom !== form.iso).map((i) => <option key={i.nom} value={i.nom}>{i.node === "local" ? i.nom : `${i.nom} — ${t("wz.src.onNode", { node: isoNodeName(i.node) })}`}</option>)}
                  </select>
                  <span className="nx-hint">{t("wz.driversHelp")} <a href="https://virtio-win.github.io/Knowledge-Base/Driver-installation.html" target="_blank" rel="noreferrer">{t("wz.driversLink")}</a></span>
                  <IsoUploadDropzone onDone={reloadIsos} labels={{ drop: t("up.dropIso"), done: t("up.done"), eta: t("up.eta"), input: t("a11y.iso_file") }} />
                </fieldset>
              )}
              <fieldset className="nx-fieldset">
                <legend>{t("wz.cleanup")}</legend>
                <label className="nx-check"><input type="checkbox" checked={form.autoCleanupEnabled} onChange={(e) => patch({ autoCleanupEnabled: e.target.checked })} /> {t("wz.cleanupLabel")}</label>
                {form.autoCleanupEnabled && (
                  <label>{t("wz.cleanupAfter")}<input className="nx-input nx-input--auto" aria-label={t("a11y.inactivity_threshold_in_days")} type="number" min={1} max={365} value={form.autoCleanupDays} onChange={(e) => patch({ autoCleanupDays: e.target.value })} {...inv("advanced", "days")} />{show("advanced", "days")}<span className="nx-hint">{t("wz.cleanupHelp")}</span></label>
                )}
              </fieldset>
              {!(form.iso && !importMode) && !form.autoCleanupEnabled && <p className="nx-muted">{t("wz.advancedNone")}</p>}
            </div>
          )}

          {stepId === "review" && (
            <div className="nx-stack">
              {review.map(([i, title, rows]) => (
                <section key={i} className="nx-subcard" aria-label={title}>
                  <div className="nx-cardhead"><h3>{title}</h3><button type="button" className="nx-btn nx-btn--ghost" onClick={() => setStep(i)}>{t("wz.change")}<span className="nx-sr"> {title}</span></button></div>
                  <dl className="nx-dl">{rows.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd className="nx-mono">{v}</dd></div>)}</dl>
                </section>
              ))}
              {!allValid && <p className="nx-notice nx-notice--warning" role="alert">{t("wz.incomplete")}</p>}
            </div>
          )}
        </div>

        <aside className="nx-wiz-recap" aria-label={t("wz.recap")}>
          <h4>{t("wz.recap")}</h4>
          <dl className="nx-dl2">
            <dt>{t("wz.r.source")}</dt><dd>{importMode ? form.importDisk && form.importDisk !== "__pending__" ? form.importDisk : "—" : form.iso ? (form.isoNode !== "local" ? `${form.iso} — ${t("wz.src.copiedFrom", { node: isoNodeName(form.isoNode) })}` : form.iso) : t("wz.src.debian")}</dd>
            <dt>{t("ct.name")}</dt><dd className="nx-mono">{form.name || "—"}</dd>
            <dt>{t("ns.node")}</dt><dd className="nx-mono">{step > 1 ? nodeName : "—"}</dd>
            <dt>{t("wz.r.cpuRam")}</dt><dd className="nx-mono">{step > 2 ? `${form.vcpu} · ${formatSizeMb(Number(form.memory_mb), lang)}` : "—"}</dd>
            <dt>{t("vh.disks")}</dt><dd className="nx-mono">{step > 3 ? form.disks.map((d, i) => (iscsiPool ? d.lun || "—" : importMode && i === 0 ? t("wz.r.imported") : `${d.size_gb} Go`)).join(" + ") : "—"}</dd>
            <dt>{t("vh.network")}</dt><dd className="nx-mono">{step > 4 ? form.network : "—"}</dd>
          </dl>
        </aside>
        </div>

        {error && <div role="alert" className="nx-error nx-wiz-error"><strong>{t("wz.failed")}</strong> <span className="nx-mono" style={{ overflowWrap: "anywhere" }}>{error}</span></div>}
        <DialogFooter className="nx-wiz-foot">
          {step > 0 && <button type="button" className="nx-btn nx-btn--ghost" disabled={busy} onClick={() => { setAttempted(false); setStep((s) => s - 1); }}>{t("wz.prev")}</button>}
          <span className="nx-sp nx-muted" style={{ fontSize: "var(--fs-12)" }}>{t("wz.stepOf", { n: step + 1, total: STEPS.length })}</span>
          <button type="button" className="nx-btn nx-btn--ghost" disabled={busy} onClick={requestClose}>{t("action.cancel")}</button>
          {stepId === "review" ? (
            <button type="button" className="nx-btn nx-btn--primary" onClick={create} disabled={busy}>{busy ? t("stor.creating") : t("wz.create")}</button>
          ) : (
            <button type="button" className="nx-btn nx-btn--primary" onClick={next}>{t("wz.next")}</button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
