import { useCallback, useEffect, useState } from "react";
import { Database, Eraser, Plus, Trash2 } from "lucide-react";
import { fetchBackupStores, createBackupStore, deleteBackupStore, cleanUpBackupStore, fetchStoragePools } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatSizeMb } from "../lib/format";
import { ErrorState } from "./States";
import { Card, Chip, Empty, Field, Loading, Meter, SideDrawer, TableWrap } from "./ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$/;
// Pools of files: a store keeps files, which ZFS, LVM and iSCSI pools (disks) do not.
const FILE_POOLS = new Set(["dir", "netfs", "fs"]);
const EMPTY = { name: "", pool: "", directory: "hyperlite-store" };

// Backup stores, as Proxmox Backup Server's datastores: each lives in a directory of a storage pool, and keeps every
// VM's disks as chunks held once. A backup job, a VM's schedule or a manual backup names one as its destination.
export default function BackupStores({ onChange }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [stores, setStores] = useState(null);
  const [pools, setPools] = useState([]);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const s = await fetchBackupStores(); setStores(s); setError(null); onChange?.(s); } catch (e) { setError(errorMessage(e)); }
  }, [onChange]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (form) fetchStoragePools().then((p) => setPools(p.filter((x) => x.node === "local" && FILE_POOLS.has(x.type)))).catch(() => setPools([]));
  }, [form]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const bad = form ? { name: !NAME_RE.test(form.name), pool: !form.pool, directory: !NAME_RE.test(form.directory) } : {};
  const invalid = Object.values(bad).some(Boolean);

  async function save() {
    setBusy(true);
    try {
      await createBackupStore({ name: form.name, pool: form.pool, directory: form.directory });
      pushToast({ kind: "success", title: t("bst.created"), message: form.name });
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("bst.createFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function cleanUp(s) {
    if (!(await confirmAction({ title: t("bst.cleanTitle", { name: s.name }), message: t("bst.cleanMsg"), confirmLabel: t("bst.clean") }))) return;
    try { await cleanUpBackupStore(s.name); pushToast({ kind: "success", title: t("bst.cleanStarted"), message: s.name }); }
    catch (e) { pushToast({ kind: "error", title: t("bst.cleanFailed"), message: errorMessage(e) }); }
  }
  async function remove(s) {
    if (!(await confirmAction({ title: t("bst.removeTitle", { name: s.name }), message: t("bst.removeMsg", { path: s.path || `${s.pool}/${s.directory}` }), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteBackupStore(s.name); pushToast({ kind: "success", title: t("bst.removed"), message: s.name }); await load(); }
    catch (e) { pushToast({ kind: "error", title: t("bst.removeFailed"), message: errorMessage(e) }); }
  }
  const gb = (bytes) => formatSizeMb(bytes / 1048576, lang);

  return (
    <>
      <Card title={t("bst.title")} flush actions={<button type="button" className="nx-btn" onClick={() => setForm({ ...EMPTY })}><Plus size={15} aria-hidden="true" />{t("bst.add")}</button>}>
        {error && !stores ? <ErrorState message={error} onRetry={load} /> : !stores ? <Loading style={{ padding: "var(--space-4)" }} /> : stores.length === 0 ? <Empty icon={Database} title={t("bst.none")} text={t("bst.noneHelp")} /> : (
          <TableWrap label={t("bst.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("bst.name")}</th><th scope="col">{t("bst.where")}</th><th scope="col">{t("bst.space")}</th><th scope="col" className="nx-num">{t("bst.backups")}</th><th scope="col">{t("bst.lastClean")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>{stores.map((s) => (
                <tr key={s.name}>
                  <th scope="row">{s.name}{!s.available && <> <Chip tone="danger">{t("bst.unreachable")}</Chip></>}</th>
                  <td className="nx-wrapcell"><span className="nx-mono">{s.pool}/{s.directory}</span>{!s.available && s.problem && <div className="nx-f-h is-error">{s.problem}</div>}</td>
                  <td>{s.available ? <><Meter value={((s.total_bytes - s.free_bytes) / s.total_bytes) * 100} label={`${t("bst.space")} ${s.name}`} /><div className="nx-f-h">{t("bst.free", { free: gb(s.free_bytes), total: gb(s.total_bytes) })}</div></> : "—"}</td>
                  <td className="nx-num nx-mono">{s.available ? s.backups : "—"}</td>
                  <td className="nx-mono">{s.last_gc ? formatDateTime(s.last_gc.at, lang) : <span className="nx-muted">{t("bst.never")}</span>}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost" disabled={!s.available} aria-label={t("bst.cleanX", { name: s.name })} onClick={() => cleanUp(s)}><Eraser size={14} aria-hidden="true" />{t("bst.clean")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("bst.removeX", { name: s.name })} onClick={() => remove(s)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <SideDrawer open={!!form} title={t("bst.add")} onClose={() => setForm(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setForm(null)}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={invalid || busy} onClick={save}>{t("bst.create")}</button>
      </>}>
        {form && <>
          <p className="nx-muted" style={{ margin: 0 }}>{t("bst.help")}</p>
          <Field label={t("bst.name")} error={form.name && bad.name ? t("bst.nameRule") : null}>{(p) => <input {...p} className="nx-inp" value={form.name} onChange={set("name")} placeholder="main" />}</Field>
          <Field label={t("bst.pool")} hint={pools.length ? t("bst.poolHint") : t("bst.noPools")}>{(p) => <select {...p} className="nx-inp" value={form.pool} onChange={set("pool")}>
            <option value="">{t("sec.choose")}</option>{pools.map((x) => <option key={x.name} value={x.name}>{x.name}{x.protocol ? ` (${x.protocol.toUpperCase()})` : ""}</option>)}
          </select>}</Field>
          <Field label={t("bst.directory")} hint={t("bst.directoryHint")} error={bad.directory ? t("bst.nameRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.directory} onChange={set("directory")} />}</Field>
        </>}
      </SideDrawer>
    </>
  );
}

// Where a backup goes: the backup directory (today's format) or a backup store. `value` is the store's name, "" for
// the directory. The stores are listed once per form.
export function DestinationField({ value, onChange, disabled, hint }) {
  const t = useT();
  const [stores, setStores] = useState([]);
  useEffect(() => { fetchBackupStores().then(setStores).catch(() => setStores([])); }, []);
  return (
    <Field label={t("bst.destination")} hint={hint}>{(p) => (
      <select {...p} className="nx-inp" disabled={disabled} value={value || ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{t("bst.toDirectory")}</option>
        {stores.map((s) => <option key={s.name} value={s.name} disabled={!s.available}>{t("bst.toStore", { name: s.name })}{s.available ? "" : ` (${t("bst.unreachable")})`}</option>)}
        {value && !stores.some((s) => s.name === value) && <option value={value}>{t("bst.toStore", { name: value })}</option>}
      </select>
    )}</Field>
  );
}
