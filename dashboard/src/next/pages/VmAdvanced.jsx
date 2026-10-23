import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { fetchVMHardwareOptions, setVMDiskOptions, setVMBootOrder, setVMBalloon, setVMMachine, fetchVMHookscript, setVMHookscript } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "../components/States";
import { Card, Chip, Field, Loading, TableWrap } from "../components/ui";

const CACHE = ["", "none", "writeback", "writethrough", "directsync", "unsafe"];
const DISCARD = ["", "ignore", "unmap"];
const IO = ["", "native", "threads", "io_uring"];
const intOrEmpty = (v, max) => v === "" || (Number.isInteger(Number(v)) && Number(v) >= 0 && Number(v) <= max);

// One disk's options: cache, discard, I/O mode and thread at the next start; throughput limits live.
function DiskRow({ vm, disk, admin, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [v, setV] = useState({ cache: disk.cache || "", discard: disk.discard || "", io: disk.io || "", iothread: disk.iothread, iops: disk.iops ?? "", mbps: disk.mbps ?? "" });
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setV((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const bad = { iops: !intOrEmpty(v.iops, 10000000), mbps: !intOrEmpty(v.mbps, 100000), io: v.io === "native" && !["none", "directsync"].includes(v.cache) };
  const dirty = v.cache !== (disk.cache || "") || v.discard !== (disk.discard || "") || v.io !== (disk.io || "") || v.iothread !== disk.iothread
    || String(v.iops) !== String(disk.iops ?? "") || String(v.mbps) !== String(disk.mbps ?? "");
  async function save() {
    setBusy(true);
    try {
      const r = await setVMDiskOptions(vm.name, disk.target, {
        cache: v.cache || null, discard: v.discard || null, io: v.io || null, iothread: v.iothread,
        iops: v.iops === "" ? null : Number(v.iops), mbps: v.mbps === "" ? null : Number(v.mbps),
      });
      pushToast({ kind: "success", title: t("va.diskSaved", { dev: disk.target }), message: r.needs_restart ? t("va.nextStart") : t("va.appliedNow") });
      onSaved(r);
    } catch (e) { pushToast({ kind: "error", title: t("va.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  const sel = (k, list, label) => (
    <select className="nx-sel" aria-label={`${label} ${disk.target}`} value={v[k]} disabled={!admin} onChange={set(k)}>
      {list.map((o) => <option key={o} value={o}>{o || t("va.default")}</option>)}
    </select>
  );
  return (
    <tr>
      <th scope="row" className="nx-mono" style={{ fontWeight: 500 }}>{disk.target}<small className="nx-muted"> {disk.bus}</small></th>
      <td>{sel("cache", CACHE, t("va.cache"))}</td>
      <td>{sel("discard", DISCARD, t("va.discard"))}</td>
      <td>{sel("io", IO, t("va.io"))}{bad.io && <small className="is-error">{t("va.nativeRule")}</small>}</td>
      <td><input type="checkbox" aria-label={`${t("va.iothread")} ${disk.target}`} checked={v.iothread} disabled={!admin || disk.bus !== "virtio"} onChange={set("iothread")} title={disk.bus !== "virtio" ? t("va.iothreadVirtio") : undefined} /></td>
      <td><input className="nx-inp nx-mono nx-inp--sm" aria-label={`IOPS ${disk.target}`} placeholder="∞" inputMode="numeric" value={v.iops} disabled={!admin} onChange={set("iops")} aria-invalid={bad.iops || undefined} /></td>
      <td><input className="nx-inp nx-mono nx-inp--sm" aria-label={`MB/s ${disk.target}`} placeholder="∞" inputMode="numeric" value={v.mbps} disabled={!admin} onChange={set("mbps")} aria-invalid={bad.mbps || undefined} /></td>
      <td>{admin && <button type="button" className="nx-btn nx-btn--sm" disabled={!dirty || busy || bad.iops || bad.mbps || bad.io} onClick={save}>{t("va.save")}</button>}</td>
    </tr>
  );
}

// Boot order: the devices that boot, first first; the others never boot.
function BootOrderCard({ vm, state, admin, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const devices = [
    ...state.disks.map((d) => ({ id: d.target, label: `${d.target} (${d.type === "cdrom" ? "CD-ROM" : t("va.disk")})` })),
    ...(state.interfaces || []).map((x) => ({ id: x, label: `${t("va.network")} ${x.slice(4)}` })),
  ];
  const [order, setOrder] = useState(state.boot_order);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setOrder(state.boot_order); }, [state.boot_order]);
  const move = (i, d) => setOrder((o) => { const n = [...o]; [n[i], n[i + d]] = [n[i + d], n[i]]; return n; });
  const toggle = (id) => setOrder((o) => (o.includes(id) ? o.filter((x) => x !== id) : [...o, id]));
  const dirty = order.join() !== state.boot_order.join();
  async function save() {
    setBusy(true);
    try { const r = await setVMBootOrder(vm.name, order); pushToast({ kind: "success", title: t("va.bootSaved"), message: t("va.bootNext") }); onSaved(r); }
    catch (e) { pushToast({ kind: "error", title: t("va.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("va.bootOrder")}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("va.bootHelp")}</p>
      <ol className="nx-bootlist">
        {order.map((id, i) => (
          <li key={id}>
            <span className="nx-mono">{i + 1}.</span> <span>{devices.find((d) => d.id === id)?.label || id}</span>
            {admin && <span className="nx-inline" style={{ marginLeft: "auto" }}>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("va.up", { dev: id })} disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size={14} aria-hidden="true" /></button>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("va.down", { dev: id })} disabled={i === order.length - 1} onClick={() => move(i, 1)}><ArrowDown size={14} aria-hidden="true" /></button>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={order.length === 1} onClick={() => toggle(id)}>{t("va.remove")}</button>
            </span>}
          </li>
        ))}
      </ol>
      {admin && devices.some((d) => !order.includes(d.id)) && (
        <div className="nx-inline" style={{ marginTop: "var(--space-2)" }}>
          <span className="nx-muted">{t("va.add")}</span>
          {devices.filter((d) => !order.includes(d.id)).map((d) => <button key={d.id} type="button" className="nx-btn nx-btn--sm" onClick={() => toggle(d.id)}>{d.label}</button>)}
        </div>
      )}
      {admin && <div className="nx-fa"><button type="button" className="nx-btn" disabled={!dirty || busy || order.length === 0} onClick={save}>{t("va.save")}</button></div>}
    </Card>
  );
}

function BalloonCard({ vm, state, admin, onSaved }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const b = state.ballooning;
  const [on, setOn] = useState(b.active);
  const [min, setMin] = useState(String(b.minimum_mb));
  const [busy, setBusy] = useState(false);
  useEffect(() => { setOn(b.active); setMin(String(b.minimum_mb)); }, [b.active, b.minimum_mb]);
  const bad = on && !(Number.isInteger(Number(min)) && Number(min) >= 256 && Number(min) <= b.memory_mb);
  const dirty = on !== b.active || (on && Number(min) !== b.minimum_mb);
  async function save() {
    setBusy(true);
    try { const r = await setVMBalloon(vm.name, { active: on, minimum_mb: on ? Number(min) : null }); pushToast({ kind: "success", title: t("va.balloonSaved"), message: r.needs_restart ? t("va.nextStart") : t("va.appliedNow") }); onSaved(r); }
    catch (e) { pushToast({ kind: "error", title: t("va.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("va.balloon")}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("va.balloonHelp")}</p>
      <label className="nx-check"><input type="checkbox" checked={on} disabled={!admin} onChange={(e) => setOn(e.target.checked)} /> {t("va.balloonOn")}</label>
      {on && <div className="nx-fg nx-fg--2" style={{ marginTop: "var(--space-3)" }}>
        <Field label={t("va.balloonMin")} help={t("hlp.balloonMin")} unit={lang === "fr" ? "Mo" : "MB"} error={bad ? t("va.balloonRule", { max: b.memory_mb }) : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={256} max={b.memory_mb} disabled={!admin} value={min} onChange={(e) => setMin(e.target.value)} />}</Field>
      </div>}
      {admin && <div className="nx-fa"><button type="button" className="nx-btn" disabled={!dirty || bad || busy} onClick={save}>{t("va.save")}</button></div>}
    </Card>
  );
}

function MachineCard({ vm, state, admin, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const next = state.newer_machines?.[0];
  async function upgrade() {
    if (!(await confirmAction({ title: t("va.machineTitle", { m: next }), message: t("va.machineMsg"), confirmLabel: t("va.machineGo") }))) return;
    try { const r = await setVMMachine(vm.name, next); pushToast({ kind: "success", title: t("va.machineSaved"), message: r.machine }); onSaved(r); }
    catch (e) { pushToast({ kind: "error", title: t("va.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("va.machine")}>
      <dl className="nx-dl2"><dt>{t("va.machineCurrent")}</dt><dd className="nx-mono">{state.machine || "—"}</dd></dl>
      <p className="nx-muted" style={{ margin: "var(--space-3) 0 0", fontSize: "var(--fs-13)" }}>{t("va.machineHelp")}</p>
      {next && admin && (
        <div className="nx-fa">
          <button type="button" className="nx-btn" aria-disabled={state.running || undefined} title={state.running ? t("menu.reason.mustStop") : undefined} onClick={() => !state.running && upgrade()}>{t("va.machineUpgrade", { m: next })}</button>
        </div>
      )}
    </Card>
  );
}

// The VM's hook script, as Proxmox VE's hookscript: picked from the node's library, which root alone fills (a script
// runs as root at every start and stop, so the dashboard never uploads one), with its last runs.
function HookScriptCard({ vm, admin }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const d = await fetchVMHookscript(vm.name, vm.node); setData(d); setChoice(d.script || ""); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [vm.name, vm.node]);
  useEffect(() => { load(); }, [load]);
  async function save() {
    setBusy(true);
    try {
      await setVMHookscript(vm.name, choice || null, vm.node);
      pushToast({ kind: "success", title: choice ? t("hk.attached", { script: choice }) : t("hk.detached") });
      await load();
    } catch (e) { pushToast({ kind: "error", title: t("va.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  const result = (r) => (r.ok ? <Chip tone="success">{t("hk.ok")}</Chip>
    : <Chip tone="warning">{r.exit_code == null ? t("hk.notRun") : t("hk.exit", { code: r.exit_code })}</Chip>);
  const library = data?.library || [];
  const runs = [...(data?.runs || [])].reverse();
  return (
    <Card title={t("hk.title")}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("hk.help")}</p>
      {error ? <p className="is-error" role="alert">{error}</p> : !data ? <Loading /> : (
        <>
          {admin ? (
            <Field label={t("hk.script")} hint={library.length ? null : t("hk.empty", { dir: "/var/lib/hyperlite/snippets" })}>{(p) => (
              <select {...p} className="nx-inp nx-mono" value={choice} onChange={(e) => setChoice(e.target.value)}>
                <option value="">{t("hk.none")}</option>
                {data.script && !library.some((s) => s.name === data.script) && <option value={data.script}>{data.script} ({t("hk.missing")})</option>}
                {library.map((s) => <option key={s.name} value={s.name} title={s.reason || undefined} disabled={!s.usable && s.name !== data.script}>{s.usable ? s.name : `${s.name} (${t("hk.unusable")})`}</option>)}
              </select>
            )}</Field>
          ) : (
            <dl className="nx-dl2"><dt>{t("hk.script")}</dt><dd className="nx-mono">{data.script || t("hk.none")}</dd></dl>
          )}
          {admin && <div className="nx-fa"><button type="button" className="nx-btn" disabled={busy || choice === (data.script || "")} onClick={save}>{t("va.save")}</button></div>}
          {library.some((s) => !s.usable) && (
            <ul className="nx-muted" style={{ margin: "var(--space-2) 0 0", paddingLeft: "var(--space-4)", fontSize: "var(--fs-13)" }}>
              {library.filter((s) => !s.usable).map((s) => <li key={s.name}><span className="nx-mono">{s.name}</span>{t("hk.sep")}{s.reason}</li>)}
            </ul>
          )}
          {runs.length > 0 && (<>
            <h3 style={{ margin: "var(--space-4) 0 var(--space-2)", fontSize: "var(--fs-14)" }}>{t("hk.runs")}</h3>
            <TableWrap label={t("hk.runs")}>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("hk.when")}</th><th scope="col">{t("hk.phase")}</th><th scope="col">{t("hk.result")}</th><th scope="col">{t("hk.output")}</th></tr></thead>
                <tbody>{runs.map((r, i) => (
                  <tr key={`${r.at}-${r.phase}-${i}`}>
                    <td className="nx-mono">{formatDateTime(r.at, lang)}</td>
                    <td className="nx-mono">{r.phase}</td>
                    <td>{result(r)} <span className="nx-muted nx-mono">{t("hk.seconds", { s: new Intl.NumberFormat(lang).format(r.seconds) })}</span></td>
                    <td>{r.output?.trim() ? <details><summary>{t("hk.show")}</summary><pre className="nx-mono" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: 0 }}>{r.output.trim()}</pre></details> : <span className="nx-muted">—</span>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </TableWrap>
          </>)}
        </>
      )}
    </Card>
  );
}

// Advanced hardware settings of a VM of this host.
export default function VmAdvancedPage({ resource: vm }) {
  const t = useT();
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { setState(await fetchVMHardwareOptions(vm.name)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [vm.name]);
  useEffect(() => { load(); }, [load]);
  if (error && !state) return <ErrorState message={error} onRetry={load} />;
  if (!state) return <Loading />;
  const onSaved = (r) => setState((s) => ({ ...s, ...r }));
  const disks = state.disks.filter((d) => d.type !== "cdrom");
  return (
    <>
      <Card title={t("va.disks")} flush>
        <p className="nx-muted" style={{ margin: "0 var(--space-4) var(--space-3)", fontSize: "var(--fs-13)" }}>{t("va.disksHelp")}</p>
        <details className="nx-explain" style={{ margin: "0 var(--space-4) var(--space-3)" }}>
          <summary>{t("hlp.disksWhat")}</summary>
          <dl>{[["va.cache", "hlp.cache"], ["va.discard", "hlp.discard"], ["va.io", "hlp.io"], ["va.iothread", "hlp.iothread"], ["IOPS", "hlp.iops"], ["MB/s", "hlp.mbps"]].map(([k, h]) => (
            <div key={k}><dt>{k.startsWith("va.") ? t(k) : k}</dt><dd>{t(h)}</dd></div>
          ))}</dl>
        </details>
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("vh.device")}</th><th scope="col">{t("va.cache")}</th><th scope="col">{t("va.discard")}</th><th scope="col">{t("va.io")}</th><th scope="col">{t("va.iothread")}</th><th scope="col">IOPS</th><th scope="col">MB/s</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>{disks.map((d) => <DiskRow key={`${d.target}:${d.cache}:${d.discard}:${d.io}:${d.iothread}:${d.iops}:${d.mbps}`} vm={vm} disk={d} admin={admin} onSaved={onSaved} />)}</tbody>
          </table>
        </TableWrap>
      </Card>
      <div className="nx-cols2">
        <BootOrderCard vm={vm} state={state} admin={admin} onSaved={onSaved} />
        <BalloonCard vm={vm} state={state} admin={admin} onSaved={onSaved} />
      </div>
      <MachineCard vm={vm} state={state} admin={admin} onSaved={onSaved} />
      <HookScriptCard vm={vm} admin={admin} />
    </>
  );
}
