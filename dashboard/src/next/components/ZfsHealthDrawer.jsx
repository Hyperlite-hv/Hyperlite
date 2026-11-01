// ZFS pools on this node's disks (docs/design/zfs-disks.md): the free disks a pool may take, and a pool's health with
// what can be done about it: a scrub, a disk attached (a mirror more, a RAIDZ grown), a new vdev, a failed disk replaced.
import { useCallback, useEffect, useState } from "react";
import { fetchZfsDisks, fetchZfsHealth, zfsPoolDisks, zfsScrub } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Chip, Field, SideDrawer } from "./ui";
import { InlineError } from "./States";

// Each layout's least number of disks (app/core/zfs_storage.py, LAYOUTS).
export const ZFS_LAYOUTS = { single: 1, mirror: 2, raidz1: 3, raidz2: 4, raidz3: 5 };

const GB = 1024 ** 3;
const short = (path) => String(path || "").split("/").pop();
const size = (bytes) => (bytes >= 1024 * GB ? `${(bytes / 1024 / GB).toFixed(1)} To` : `${Math.round(bytes / GB)} Go`);
const tone = (state) => (state === "ONLINE" || state === "HEALTHY" ? "success" : state === "DEGRADED" ? "warning" : "danger");

export function ZfsDiskPicker({ disks, chosen, onChange, error, min, single = false }) {
  const t = useT();
  if (disks === null) return <p className="nx-hint">{t("zfs.loadingDisks")}</p>;
  if (!disks.length) return <p className="nx-hint" role="status">{t("zfs.noFreeDisk")}</p>;
  const toggle = (path, on) => onChange(single ? (on ? [path] : []) : on ? [...chosen, path] : chosen.filter((x) => x !== path));
  return (
    <fieldset className="nx-f">
      <legend className="nx-f-label">{t(single ? "zfs.disk" : "zfs.disks", { n: min })}</legend>
      {disks.map((d) => (
        <label key={d.path} className="nx-check" title={d.path}>
          <input type={single ? "radio" : "checkbox"} name="zfs-disk" checked={chosen.includes(d.path)} onChange={(e) => toggle(d.path, e.target.checked)} />
          {" "}<span className="nx-mono">{short(d.device)}</span> · {size(d.size)} · {d.model || short(d.path)} · {t(d.rotational ? "zfs.hdd" : "zfs.ssd")}
        </label>
      ))}
      <span className="nx-f-h">{t("zfs.eraseHint")}</span>
      {error && <span className="nx-f-h is-error" role="alert">{error}</span>}
    </fieldset>
  );
}

function VdevRows({ vdevs, depth, onReplace, admin }) {
  const t = useT();
  return vdevs.flatMap((v) => {
    const leaf = !v.children.length;
    const errs = v.read_errors + v.write_errors + v.checksum_errors;
    return [
      <tr key={`${depth}-${v.path || v.name}`}>
        <td style={{ paddingLeft: `${0.75 + depth * 1.25}rem` }} className={leaf ? "nx-mono" : undefined} title={v.path || undefined}>{leaf ? short(v.path || v.name) : v.name}{v.class ? ` (${t(`zfs.class.${v.class}`)})` : ""}</td>
        <td><Chip tone={tone(v.state)}>{v.state}</Chip></td>
        <td className="nx-mono" title={t("zfs.errorsTitle")}>{v.read_errors} / {v.write_errors} / {v.checksum_errors}</td>
        <td>{leaf && admin && (v.state !== "ONLINE" || errs > 0 ? <button type="button" className="nx-btn nx-btn--sm" onClick={() => onReplace(v.path || v.name)}>{t("zfs.replace")}</button> : <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => onReplace(v.path || v.name)}>{t("zfs.replace")}</button>)}</td>
      </tr>,
      ...VdevRows({ vdevs: v.children, depth: depth + 1, onReplace, admin }),
    ];
  });
}

export default function ZfsHealthDrawer({ pool, onClose, admin }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState(null); // {kind: "attach"|"add"|"replace", target}
  const [disks, setDisks] = useState(null);
  const [chosen, setChosen] = useState([]);
  const [layout, setLayout] = useState("mirror");

  const load = useCallback(() => {
    if (!pool) return;
    fetchZfsHealth(pool).then((h) => { setHealth(h); setError(null); }).catch((e) => setError(errorMessage(e)));
  }, [pool]);
  useEffect(() => { setHealth(null); setAction(null); load(); }, [load]);
  // A scrub or a rebuild runs for a while: its progress is read again meanwhile.
  const running = health?.scan?.state === "SCANNING";
  useEffect(() => { if (!running) return undefined; const id = setInterval(load, 5000); return () => clearInterval(id); }, [running, load]);
  useEffect(() => { if (action) { setDisks(null); setChosen([]); fetchZfsDisks().then((r) => setDisks(r.disks || [])).catch(() => setDisks([])); } }, [action]);

  async function run(fn, done) {
    setBusy(true);
    try { const h = await fn(); if (h) setHealth(h); pushToast({ kind: "success", title: done, message: pool }); setAction(null); load(); }
    catch (e) { pushToast({ kind: "error", title: t("zfs.failed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  async function applyDisks() {
    const need = action.kind === "add" ? ZFS_LAYOUTS[layout] : 1;
    if (chosen.length < need) return;
    if (!(await confirmAction({ title: t("zfs.eraseTitle"), message: t("zfs.eraseMsg", { disks: chosen.join(", ") }), confirmLabel: t("zfs.eraseConfirm"), danger: true }))) return;
    await run(() => zfsPoolDisks(pool, { action: action.kind, disks: chosen, layout: action.kind === "add" ? layout : null, target: action.target || null }), t(`zfs.done.${action.kind}`));
  }

  const scan = health?.scan;
  const top = (health?.vdevs || []).filter((v) => !v.class);
  return (
    <SideDrawer open={!!pool} title={t("zfs.healthOf", { name: pool || "" })} onClose={onClose} busy={busy} footer={<button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.close")}</button>}>
      {error && <InlineError message={error} onRetry={load} />}
      {!health && !error && <p className="nx-hint">{t("zfs.loading")}</p>}
      {health && <>
        <p><Chip tone={tone(health.state)}>{health.state}</Chip> {health.errors > 0 && <span className="is-error">{t("zfs.dataErrors", { n: health.errors })}</span>}</p>
        {health.status && <p className="nx-hint">{health.status}</p>}
        {health.action && <p className="nx-hint">{health.action}</p>}
        <p className="nx-hint" role="status">{!scan || !scan.function ? t("zfs.neverScrubbed")
          : running ? t(scan.function === "RESILVER" ? "zfs.resilvering" : "zfs.scrubbing", { pct: scan.to_examine ? Math.floor((100 * (scan.examined || 0)) / scan.to_examine) : 0 })
          : t(scan.function === "RESILVER" ? "zfs.lastResilver" : "zfs.lastScrub", { when: scan.end_time ? new Date(scan.end_time * 1000).toLocaleString() : "?", errors: scan.errors || 0 })}</p>
        <table className="nx-table">
          <thead><tr><th>{t("zfs.device")}</th><th>{t("zfs.state")}</th><th>{t("zfs.errors")}</th><th /></tr></thead>
          <tbody><VdevRows vdevs={health.vdevs} depth={0} admin={admin} onReplace={(target) => setAction({ kind: "replace", target })} /></tbody>
        </table>
        {admin && <div className="nx-row" style={{ display: "flex", gap: ".5rem", flexWrap: "wrap", marginTop: ".75rem" }}>
          <button type="button" className="nx-btn nx-btn--sm" disabled={busy || (running && scan.function === "RESILVER")} onClick={() => run(() => zfsScrub(pool, running), t(running ? "zfs.scrubStopped" : "zfs.scrubStarted"))}>{t(running ? "zfs.stopScrub" : "zfs.scrub")}</button>
          {top.map((v) => <button key={v.name} type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy} title={t(`zfs.attachHint.${v.type === "raidz" ? "raidz" : v.type === "mirror" ? "mirror" : "disk"}`)} onClick={() => setAction({ kind: "attach", target: v.children.length ? v.name : v.path || v.name })}>{t("zfs.attach", { name: v.children.length ? v.name : short(v.path || v.name) })}</button>)}
          <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy} title={t("zfs.addHint")} onClick={() => setAction({ kind: "add" })}>{t("zfs.add")}</button>
        </div>}
        {action && <div className="nx-card" style={{ marginTop: "1rem", padding: ".75rem" }}>
          <strong>{action.kind === "replace" ? t("zfs.replaceTitle", { disk: short(action.target) }) : action.kind === "attach" ? t("zfs.attachTitle", { name: short(action.target) }) : t("zfs.addTitle")}</strong>
          {action.kind === "add" && <Field label={t("zfs.layout")} hint={t(`zfs.layoutHelp.${layout}`)}>{(p) => <select {...p} className="nx-inp" value={layout} onChange={(e) => setLayout(e.target.value)}>{Object.keys(ZFS_LAYOUTS).map((l) => <option key={l} value={l}>{t(`zfs.layout.${l}`)}</option>)}</select>}</Field>}
          <ZfsDiskPicker disks={disks} chosen={chosen} onChange={setChosen} single={action.kind !== "add"} min={action.kind === "add" ? ZFS_LAYOUTS[layout] : 1} />
          <div style={{ display: "flex", gap: ".5rem", marginTop: ".5rem" }}>
            <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setAction(null)} disabled={busy}>{t("action.cancel")}</button>
            <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" disabled={busy || chosen.length < (action.kind === "add" ? ZFS_LAYOUTS[layout] : 1)} onClick={applyDisks}>{t(`zfs.apply.${action.kind}`)}</button>
          </div>
        </div>}
      </>}
    </SideDrawer>
  );
}
