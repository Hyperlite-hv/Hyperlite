import { useEffect, useRef, useState } from "react";
import { readOva, extractOva, createVMTask, fetchStoragePools, fetchNetworks, fetchTaskDetail } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { SideDrawer, Field, Chip, Loading } from "./ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;
// A VM name from the OVA's: what Hyperlite names refuse (spaces, dots, underscores) becomes a dash.
const vmNameOf = (name) => String(name || "").replace(/[^a-zA-Z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63) || "imported-vm";
// The storage kinds a VM's disks are made on from imported files (iSCSI takes whole LUNs, not files).
const POOL_TYPES = ["dir", "netfs", "zfs", "logical"];

// A VM exported from VMware as an OVA (app/core/ova.py): its settings read from the descriptor, its disks copied out of
// the archive (checked against the manifest), then the VM created from them, as Proxmox VE's import does.
export default function OvaImportDrawer({ file, onClose, onDone }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ name: "", vcpu: "", memory_mb: "", pool: "", network: "" });
  const [pools, setPools] = useState([]);
  const [networks, setNetworks] = useState([]);
  const [step, setStep] = useState(null);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  useEffect(() => {
    if (!file) return;
    setInfo(null); setError(null); setStep(null);
    readOva(file).then((r) => {
      setInfo(r);
      setForm((f) => ({ ...f, name: vmNameOf(r.name), vcpu: String(r.vcpu), memory_mb: String(r.memory_mb) }));
    }).catch((e) => setError(errorMessage(e)));
    fetchStoragePools().then((l) => {
      const usable = (l || []).filter((p) => p.node === "local" && p.state === "active" && POOL_TYPES.includes(p.type));
      setPools(usable);
      setForm((f) => ({ ...f, pool: f.pool || usable.find((p) => p.name === "default")?.name || usable[0]?.name || "" }));
    }).catch(() => setPools([]));
    fetchNetworks().then((l) => {
      setNetworks(l || []);
      setForm((f) => ({ ...f, network: f.network || (l || []).find((n) => n.active)?.name || "" }));
    }).catch(() => setNetworks([]));
  }, [file]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const nameBad = form.name !== "" && !NAME_RE.test(form.name);
  const vcpuBad = !(Number.isInteger(Number(form.vcpu)) && Number(form.vcpu) >= 1);
  const memoryBad = !(Number.isInteger(Number(form.memory_mb)) && Number(form.memory_mb) >= 64);
  const ready = info && form.name && !nameBad && !vcpuBad && !memoryBad && form.pool && form.network && !step;

  async function waitTask(id) {
    for (;;) {
      if (!alive.current) return { status: "abandonne" };
      const task = await fetchTaskDetail(id);
      if (task.status !== "running") return task;
      await new Promise((r) => setTimeout(r, 1500));
    }
  }

  async function submit() {
    if (!ready) return;
    try {
      setStep("extract");
      const { task_id: extractId } = await extractOva(file);
      const extracted = await waitTask(extractId);
      if (extracted.status !== "succeeded") throw new Error(extracted.error || t("err.unknown"));
      setStep("create");
      const [first, ...rest] = info.disks;
      const { task_id: createId } = await createVMTask({
        name: form.name, vcpu: Number(form.vcpu), memory_mb: Number(form.memory_mb), firmware: info.firmware,
        guest_os: info.guest_os, import_disk: first.import_disk, storage_pool: form.pool, network: form.network,
        disks: [{ size_gb: first.capacity_gb }, ...rest.map((d) => ({ size_gb: d.capacity_gb, import_disk: d.import_disk }))],
      });
      const created = await waitTask(createId);
      if (created.status !== "succeeded") throw new Error(created.error || t("err.unknown"));
      pushToast({ kind: "success", title: t("ova.created", { name: form.name }), message: t("ova.createdHelp") });
      onDone?.(); onClose();
    } catch (e) {
      if (alive.current) pushToast({ kind: "error", title: t("ova.failed"), message: errorMessage(e), duration: Infinity });
    } finally {
      if (alive.current) setStep(null);
    }
  }

  const firmwareLabel = info ? t(`ova.fw.${info.firmware}`) : "";
  return (
    <SideDrawer open={!!file} title={t("ova.title", { file })} onClose={onClose} busy={!!step} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={!!step}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!ready} onClick={submit}>{t("ova.create")}</button>
    </>}>
      {error ? <div className="nx-bn" data-tone="error" role="alert"><span className="nx-bn-t">{error}</span></div>
        : !info ? <Loading /> : (
          <>
            <p className="nx-hint" style={{ margin: 0 }}>{t("ova.help")}</p>
            <div className="nx-inline" style={{ flexWrap: "wrap", gap: "var(--space-2)" }}>
              {info.os && <Chip>{info.os}</Chip>}
              <Chip>{firmwareLabel}</Chip>
              <Chip>{t(`ova.family.${info.guest_os}`)}</Chip>
            </div>
            <Field label={t("ova.name")} error={nameBad ? t("ova.nameRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.name} onChange={set("name")} />}</Field>
            <div className="nx-fg">
              <Field label={t("ova.vcpu")} error={vcpuBad ? t("ova.numberRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={1} value={form.vcpu} onChange={set("vcpu")} />}</Field>
              <Field label={t("ova.memory")} unit="MiB" error={memoryBad ? t("ova.memoryRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={64} value={form.memory_mb} onChange={set("memory_mb")} />}</Field>
            </div>
            <Field label={t("ova.disks")}>{() => (
              <div>{info.disks.map((d) => (
                <div key={d.file} className="nx-inline" style={{ justifyContent: "space-between", gap: "var(--space-3)" }}>
                  <span className="nx-mono" style={{ overflowWrap: "anywhere" }}>{d.file}</span>
                  <span className="nx-muted nx-mono" style={{ flex: "none" }}>{t("ova.diskSize", { gb: d.capacity_gb })}</span>
                </div>
              ))}</div>
            )}</Field>
            <Field label={t("ova.pool")} hint={t("ova.poolHelp")}>{(p) => <select {...p} className="nx-inp" value={form.pool} onChange={set("pool")}>{pools.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}</select>}</Field>
            <Field label={t("ova.network")} hint={info.nics.length > 1 ? t("ova.nicsMore", { n: info.nics.length, names: info.nics.map((n) => n.network).filter(Boolean).join(", ") }) : info.nics[0]?.network ? t("ova.nicFrom", { name: info.nics[0].network }) : null}>{(p) => <select {...p} className="nx-inp" value={form.network} onChange={set("network")}>{networks.map((n) => <option key={n.name} value={n.name}>{n.name}</option>)}</select>}</Field>
            {step && <p role="status" aria-live="polite">{t(step === "extract" ? "ova.extracting" : "ova.creating")}</p>}
          </>
        )}
    </SideDrawer>
  );
}
