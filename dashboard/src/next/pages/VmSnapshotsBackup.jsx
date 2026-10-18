import { useCallback, useEffect, useRef, useState } from "react";
import BackupFileBrowser from "../components/BackupFileBrowser";
import RetentionFields, { retentionForm, retentionPayload, retentionProblems } from "../components/RetentionFields";
import {
  fetchSnapshots, createSnapshot, restoreSnapshot, deleteSnapshot, fetchTaskDetail,
  fetchVMBackups, createBackup, deleteBackup, restoreBackup, verifyBackup, fetchBackupSchedule, setBackupSchedule, deleteBackupSchedule,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { promptText } from "../../store/usePromptStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { useIntent } from "../lib/intents";
import { errorMessage } from "../lib/errors";
import { formatSizeMb, formatDateTime } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { ErrorState } from "../components/States";
import { PageHeader, Card, Empty, Field, Loading, TableWrap } from "../components/ui";
import { Archive, Camera, Info, LoaderCircle, Trash2, TriangleAlert } from "lucide-react";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;
const SNAP_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$/;
const taskWire = (s) => (s === "succeeded" ? "succeeded" : s === "failed" ? "failed" : "running");

const nowMs = () => Date.now();
const elapsed = (from) => { const s = Math.max(0, Math.round((nowMs() - from) / 1000)); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`; };

// ---- Snapshots -------------------------------------------------------------------------------------------

// create / restore answer 202 + a task id: the page follows the real task (bounded at 5 minutes, stopped when
// the page is left) instead of inventing a percentage libvirt does not expose.
export function VmSnapshotsPage({ resource: vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const [snaps, setSnaps] = useState(null);
  const [error, setError] = useState(null);
  const [job, setJob] = useState(null);
  const [, tick] = useState(0);
  const alive = useRef(true);
  const vmName = vm?.name;

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (!job) return undefined; const id = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(id); }, [job]);

  const reload = useCallback(async () => {
    try { const r = await fetchSnapshots(vmName); setSnaps(Array.isArray(r) ? r : []); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [vmName]);
  useEffect(() => { if (vmName) reload(); }, [vmName, reload]);
  // VM ▸ Actions ▸ Create a snapshot opens this tab and starts the creation dialog once the list is loaded.
  useIntent("snapshot", (name) => { if (name === vmName) document.getElementById("vs-create")?.click(); }, snaps != null);

  const waitTask = useCallback(async (id) => {
    for (let i = 0; i < 300; i++) {
      if (!alive.current) return { status: "abandonne" };
      const task = await fetchTaskDetail(id);
      if (task.status !== "running") return task;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return { status: "failed", error: t("vs.stillRunning") };
  }, [t]);

  if (!vm) return null;
  if (error && snaps == null) return <ErrorState message={error} onRetry={reload} />;
  const zfs = Boolean(vm.zfs_storage);
  const busy = job != null;
  // QEMU cannot snapshot a running VM whose UEFI firmware is in pflash; the server refuses it too.
  const uefiRunning = !zfs && vm.state === "active" && Boolean(vm.firmware) && vm.firmware !== "bios";
  const list = snaps || [];

  async function run(label, start, okTitle, failTitle) {
    setJob({ label, startedAt: nowMs() });
    try {
      const { task_id } = await start();
      const done = await waitTask(task_id);
      if (done.status === "succeeded") pushToast({ kind: "success", title: okTitle });
      else if (done.status !== "abandonne") pushToast({ kind: "error", title: failTitle, message: done.error || t("err.unknown") });
      if (alive.current) await reload();
    } catch (e) { pushToast({ kind: "error", title: failTitle, message: errorMessage(e) }); }
    finally { if (alive.current) setJob(null); }
  }

  async function create() {
    const auto = `snap-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}`;
    const name = await promptText({ title: t("vs.createTitle"), label: t("vs.name"), defaultValue: auto, confirmLabel: t("vs.create"), validate: (v) => (!SNAP_RE.test(v) ? t("vs.nameRule") : list.some((s) => s.name === v) ? t("vs.nameTaken") : "") });
    if (!name) return;
    run(t("vs.creating", { name }), () => createSnapshot(vm.name, name.trim(), "Created from the dashboard"), t("vs.created"), t("vs.createFailed"));
  }
  async function restore(s) {
    const running = vm.state === "active";
    if (!(await confirmAction({ title: t("vs.restoreTitle", { name: s.name }), message: t("vs.restoreMsg") + (running && s.vm_state === "stopped" ? ` ${t("vs.restoreRunning")}` : ""), confirmLabel: t("vs.restore"), danger: true }))) return;
    run(t("vs.restoring", { name: s.name }), () => restoreSnapshot(vm.name, s.name), t("vs.restored"), t("vs.restoreFailed"));
  }
  async function remove(s) {
    if (!(await confirmAction({ title: t("vs.deleteTitle", { name: s.name }), message: t("vs.deleteMsg"), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    try { await deleteSnapshot(vm.name, s.name); pushToast({ kind: "success", title: t("vs.deleted"), message: s.name }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("vs.deleteFailed"), message: errorMessage(e) }); }
  }

  const depthOf = (s, seen = new Set()) => { if (!s.parent || seen.has(s.name)) return 0; seen.add(s.name); const p = list.find((x) => x.name === s.parent); return p ? 1 + depthOf(p, seen) : 0; };
  const kind = (s) => (s.vm_state === "disk_only" ? t("vs.zfsDisk") : s.vm_state === "running" ? t("vs.withMemory") : s.vm_state ? t("vs.diskOnly") : "—");

  const sorted = [...list].sort((x, y) => Number(y.created_at || 0) - Number(x.created_at || 0));
  return (
    <>
      <PageHeader level={2} title={t("tab.snapshots")} count={snaps ? list.length : null} desc={t("vs.desc")}
        actions={caps.admin && <button id="vs-create" type="button" className="nx-btn nx-btn--primary" disabled={busy || uefiRunning} onClick={create}><Camera size={15} aria-hidden="true" />{t("vs.create")}</button>} />
      {uefiRunning && <div className="nx-bn" data-tone="info" role="status"><Info size={16} aria-hidden="true" /><span className="nx-bn-t">{t("vs.uefiStop")}</span></div>}
      {list.length >= 3 && <div className="nx-bn" data-tone="warning" role="status"><TriangleAlert size={16} aria-hidden="true" /><span className="nx-bn-t">{t(zfs ? "vs.manyZfs" : "vs.manyQcow", { n: list.length })}</span></div>}
      {job && (
        <div className="nx-bn" data-tone="info" role="status" aria-live="polite">
          <LoaderCircle size={16} className="nx-spin" aria-hidden="true" />
          <span className="nx-bn-t"><b>{job.label}</b> <span className="nx-mono nx-muted">{elapsed(job.startedAt)}</span><br /><span className="nx-muted">{zfs ? t("vs.zfsNote") : t("vs.qcowNote")}</span></span>
        </div>
      )}
      <div className="nx-card2">
        <div className="nx-card2-b">
          {snaps == null ? <Loading style={{ margin: 0 }} /> : (
            <ol className="nx-tl" aria-label={t("vs.timeline")}>
              <li className="nx-tl-it is-now"><b>{t("vs.now")}</b><div className="nx-tl-m">{t("vs.nowSub")}</div></li>
              {sorted.map((s) => (
                <li key={s.name} className="nx-tl-it" style={{ marginLeft: `${depthOf(s) * 1.25}rem` }}>
                  <div className="nx-inline" style={{ alignItems: "flex-start" }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <b className="nx-mono">{s.name}</b> {s.current && <span className="nx-chip" data-tone="accent">{t("vs.current")}</span>}
                      <div className="nx-tl-m">{[formatDateTime(s.created_at, lang), kind(s), s.parent ? t("vs.after", { name: s.parent }) : null].filter(Boolean).join(" · ")}</div>
                      {s.description && <div className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{s.description}</div>}
                    </div>
                    {caps.admin && <div className="nx-ra">
                      <button type="button" className="nx-btn nx-btn--sm" disabled={busy} aria-label={t("a11y.restore_snapshot_x", { v: s.name })} onClick={() => restore(s)}>{t("vs.restore")}</button>
                      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" disabled={busy} aria-label={t("a11y.delete_snapshot_x", { v: s.name })} title={t("menu.delete").replace("…", "")} onClick={() => remove(s)}><Trash2 size={15} aria-hidden="true" /></button>
                    </div>}
                  </div>
                </li>
              ))}
            </ol>
          )}
          {snaps && list.length === 0 && <Empty icon={Camera} title={t("vs.none")} text={caps.admin ? t("vs.noneHelp") : null} />}
        </div>
      </div>
    </>
  );
}

// ---- Backups ---------------------------------------------------------------------------------------------
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
export function VmBackupPage({ resource: vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const [backups, setBackups] = useState(null);
  const [error, setError] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [form, setForm] = useState({ frequency: "daily", time: "02:00", ...retentionForm(null) });
  const [busy, setBusy] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [browsing, setBrowsing] = useState(null);
  const vmName = vm?.name;
  // In a cluster the VM's own node keeps its backups: every request names it, and this node sends it there.
  const vmNode = vm?.node && vm.node !== "local" ? vm.node : undefined;

  const reload = useCallback(async () => {
    if (!vmName) return;
    try { const b = await fetchVMBackups(vmName, vmNode); setBackups(Array.isArray(b) ? b : []); setError(null); } catch (e) { setError(errorMessage(e)); }
    fetchBackupSchedule(vmName).then((s) => { setSchedule(s || null); if (s) setForm({ frequency: s.frequency, time: s.time, ...retentionForm(s) }); }).catch(() => {});
  }, [vmName, vmNode]);
  useEffect(() => { reload(); }, [reload]);
  const running = Boolean(backups?.some((b) => b.status !== "succeeded" && b.status !== "failed"));
  useEffect(() => { if (!running) return undefined; const id = setInterval(reload, 4000); return () => clearInterval(id); }, [running, reload]);

  if (!vm) return null;
  if (error && backups == null) return <ErrorState message={error} onRetry={reload} />;
  const list = backups || [];
  const stopped = vm.state !== "active";
  const retention = retentionPayload(form);
  const problems = { time: !TIME_RE.test(form.time || ""), retention: Object.keys(retentionProblems(form)).length > 0 };
  const dirty = !schedule || schedule.frequency !== form.frequency || schedule.time !== form.time
    || ["retention_count", "keep_days", "keep_weeks", "keep_months"].some((k) => (schedule[k] ?? null) !== retention[k]);
  const fail = (title) => (e) => pushToast({ kind: "error", title, message: errorMessage(e) });

  async function now() {
    setBusy(true);
    try { await createBackup(vm.name, null, vmNode); pushToast({ kind: "success", title: t("vb.started"), message: vm.name }); setTimeout(reload, 1500); } catch (e) { fail(t("vb.startFailed"))(e); } finally { setBusy(false); }
  }
  async function save(e) {
    e?.preventDefault();
    if (problems.time || problems.retention) return;
    setBusy(true);
    try { const s = await setBackupSchedule(vm.name, { frequency: form.frequency, time: form.time, ...retention }, vmNode); setSchedule(s); setEnabling(false); pushToast({ kind: "success", title: t("vb.scheduleSaved"), message: `${t(`vb.f.${form.frequency}`)} · ${form.time} UTC` }); }
    catch (er) { fail(t("vb.scheduleFailed"))(er); } finally { setBusy(false); }
  }
  async function disable() {
    if (!(await confirmAction({ title: t("vb.disableTitle"), message: t("vb.disableMsg", { name: vm.name }), confirmLabel: t("nt.disable"), danger: true }))) return;
    setBusy(true);
    try { await deleteBackupSchedule(vm.name); setSchedule(null); pushToast({ kind: "success", title: t("vb.scheduleOff"), message: vm.name }); } catch (er) { fail(t("vb.scheduleFailed"))(er); } finally { setBusy(false); }
  }
  async function remove(b) {
    if (!(await confirmAction({ title: t("vb.deleteTitle", { id: b.id }), message: t("vb.deleteMsg"), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    try { await deleteBackup(b.id, vmNode); pushToast({ kind: "success", title: t("vb.deleted"), message: `#${b.id}` }); await reload(); } catch (er) { fail(t("vb.deleteFailed"))(er); }
  }
  async function restoreInPlace(b) {
    if (!stopped) return;
    if (!(await confirmAction({ title: t("vb.overwriteTitle", { name: vm.name }), message: t("vb.overwriteMsg"), confirmLabel: t("vs.restore"), danger: true }))) return;
    try { await restoreBackup(b.id, "overwrite", null, vmNode); pushToast({ kind: "success", title: t("vb.restoreStarted") }); await reload(); } catch (er) { fail(t("vb.restoreFailed"))(er); }
  }
  async function restoreNew(b) {
    const taken = new Set(vms.map((v) => v.name));
    const name = await promptText({ title: t("vb.newTitle", { id: b.id }), label: t("tp.newName"), defaultValue: `${vm.name}-restored`, confirmLabel: t("vs.restore"), validate: (v) => (!NAME_RE.test(v) ? t("ct.nameRule") : taken.has(v) ? t("tp.nameTaken") : "") });
    if (!name) return;
    try { await restoreBackup(b.id, "new", name.trim(), vmNode); pushToast({ kind: "success", title: t("vb.restoreStarted"), message: name.trim() }); } catch (er) { fail(t("vb.restoreFailed"))(er); }
  }
  async function verify(b) {
    setBusy(true);
    try {
      const { task_id } = await verifyBackup(b.id, vmNode);
      pushToast({ kind: "info", title: t("vb.verifying"), message: `#${b.id}` });
      // A large backup takes a while to read: poll its task, then show the recorded result.
      for (let i = 0; i < 720; i += 1) {
        await new Promise((r) => setTimeout(r, 2500));
        const task = await fetchTaskDetail(task_id, vmNode).catch(() => null);
        if (task && task.status !== "running") break;
      }
      const fresh = (await fetchVMBackups(vm.name, vmNode)).find((x) => x.id === b.id);
      await reload();
      if (fresh?.verification === "verified") pushToast({ kind: "success", title: t("vb.verified"), message: `#${b.id}` });
      else pushToast({ kind: "error", title: t("vb.corrupt"), message: fresh?.verification_detail || t("err.unknown") });
    } catch (er) { fail(t("vb.verifyFailed"))(er); } finally { setBusy(false); }
  }
  const size = (bytes) => (bytes ? formatSizeMb(bytes / 1048576, lang) : "—");
  const integrity = (b) => (b.status !== "succeeded" ? null
    : b.verification === "verified" ? <span className="nx-chip" data-tone="success" title={t("vb.verifiedOn", { date: formatDateTime(b.verified_at, lang) })}>{t("vb.v.ok")}</span>
    : b.verification === "corrupted" ? <span className="nx-chip" data-tone="danger" title={b.verification_detail || ""}>{t("vb.v.bad")}</span>
    : <span className="nx-chip" title={t("vb.v.neverHelp")}>{t("vb.v.never")}</span>);

  return (
    <>
      <PageHeader level={2} title={t("tab.backups")} count={backups ? list.length : null} desc={t("vb.desc")}
        actions={caps.admin && <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={now}><Archive size={15} aria-hidden="true" />{t("vb.now")}</button>} />
      <div className="nx-cols2">
        <Card title={t("vb.history")} flush={list.length > 0}>
          {caps.admin && !stopped && list.some((b) => b.status === "succeeded") && <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("vb.stopToRestore")}</p>}
          {backups == null ? <Loading style={{ margin: 0 }} /> : list.length === 0 ? <Empty icon={Archive} title={t("vb.none")} text={t("vb.noneHelp")} /> : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("vm.created")}</th><th scope="col">{t("vb.mode")}</th><th scope="col" className="nx-num">{t("ct.size")}</th><th scope="col">{t("vb.integrity")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {list.map((b) => (
                    <tr key={b.id}>
                      <td><StatusIndicator kind="task" wire={taskWire(b.status)} /></td>
                      <td className="nx-mono">{formatDateTime(b.created_at, lang)}{b.error && <div className="nx-f-h is-error nx-wrapcell">{b.error}</div>}</td>
                      <td>{b.mode === "live" ? t("vb.hot") : t("vb.cold")}</td>
                      <td className="nx-num nx-mono">{size(b.size_bytes)}</td>
                      <td>{integrity(b)}{b.verification === "corrupted" && b.verification_detail && <div className="nx-f-h is-error nx-wrapcell">{b.verification_detail}</div>}</td>
                      <td><div className="nx-ra">
                        {caps.admin && b.status === "succeeded" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy} aria-label={t("vb.verifyAria", { v: b.id })} onClick={() => verify(b)}>{t("vb.verify")}</button>}
                        {caps.admin && b.status === "succeeded" && <button type="button" className="nx-btn nx-btn--sm" aria-disabled={!stopped || undefined} title={!stopped ? t("vb.stopToRestore") : undefined} aria-label={t("a11y.restore_backup_x_in_place", { v: b.id })} onClick={() => restoreInPlace(b)}>{t("vb.inPlace")}</button>}
                        {caps.admin && b.status === "succeeded" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("a11y.restore_backup_x_to_a_new_vm", { v: b.id })} onClick={() => restoreNew(b)}>{t("vb.newVm")}</button>}
                        {caps.admin && b.status === "succeeded" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("fr.browseX", { v: b.id })} onClick={() => setBrowsing({ ...b, vm_name: vm.name, on: vmNode })}>{t("fr.browse")}</button>}
                        {caps.admin && (b.status === "succeeded" || b.status === "failed") && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_backup_x", { v: b.id })} title={t("menu.delete").replace("…", "")} onClick={() => remove(b)}><Trash2 size={15} aria-hidden="true" /></button>}
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
        <Card title={t("vb.planning")}>
          <div className="nx-inline" style={{ marginBottom: "var(--space-4)" }}>
            <button type="button" className="nx-sw" role="switch" aria-checked={Boolean(schedule) || enabling} aria-label={t("vb.scheduleSwitch")} disabled={!caps.admin || busy} onClick={() => (schedule ? disable() : setEnabling((v) => !v))} />
            <span>{schedule ? t("vb.enabled") : enabling ? t("vb.toEnable") : t("vb.disabled")}</span>
          </div>
          <div className="nx-fg nx-fg--1">
            <Field label={t("vb.frequency")}>{(p) => <select {...p} className="nx-inp" aria-label={t("a11y.backup_frequency")} disabled={!caps.admin} value={form.frequency} onChange={(e) => setForm({ ...form, frequency: e.target.value })}><option value="daily">{t("vb.f.daily")}</option><option value="weekly">{t("vb.f.weekly")}</option><option value="monthly">{t("vb.f.monthly")}</option></select>}</Field>
            <Field label={t("vb.time")} hint={t("vb.utc")} error={problems.time ? t("vb.timeRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.backup_time")} type="time" disabled={!caps.admin} value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} />}</Field>
          </div>
          <RetentionFields value={form} disabled={!caps.admin} onChange={(v) => setForm({ ...form, ...v })} />
          {caps.admin && (
            <div className="nx-fa">
              <span className="nx-fa-l">{schedule?.next_run ? `${t("vb.next")} : ${formatDateTime(schedule.next_run, lang)}` : ""}{schedule?.last_run ? ` · ${t("vb.last")} ${formatDateTime(schedule.last_run, lang)}` : ""}</span>
              <button type="button" className="nx-btn" disabled={busy || (!dirty && Boolean(schedule)) || problems.time || problems.retention} onClick={save}>{t("sso.save")}</button>
            </div>
          )}
        </Card>
      </div>
      {browsing && <BackupFileBrowser backup={browsing} onClose={() => setBrowsing(null)} />}
    </>
  );
}
