import { useCallback, useEffect, useState } from "react";
import { Database, Eraser, Plus, Trash2 } from "lucide-react";
import { fetchBackupStores, createBackupStore, deleteBackupStore, cleanUpBackupStore, fetchStoragePools, fetchBackupRemotes, fetchRemoteStores, unlockBackupStore } from "../../api/client";
import { promptText } from "../../store/usePromptStore";
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
const EMPTY = { name: "", pool: "", directory: "hyperlite-store", encrypted: false, master_key: "none", master_public_key: "", key: "", copy_of: "", remote: "", remote_store: "" };

function download(name, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

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
  // The keys of a store just made: shown once, never again (the API does not keep them readable).
  const [shown, setShown] = useState(null);
  const [remotes, setRemotes] = useState([]);
  const [remoteStores, setRemoteStores] = useState(null);
  const [saved, setSaved] = useState(false);
  const load = useCallback(async () => {
    try { const s = await fetchBackupStores(); setStores(s); setError(null); onChange?.(s); } catch (e) { setError(errorMessage(e)); }
  }, [onChange]);
  useEffect(() => { load(); }, [load]);
  const formOpen = Boolean(form);
  useEffect(() => {
    if (!formOpen) return;
    fetchStoragePools().then((p) => setPools(p.filter((x) => x.node === "local" && FILE_POOLS.has(x.type)))).catch(() => setPools([]));
    fetchBackupRemotes().then(setRemotes).catch(() => setRemotes([]));
  }, [formOpen]);
  const remote = form?.remote;
  useEffect(() => {
    setRemoteStores(null);
    if (remote) fetchRemoteStores(remote).then(setRemoteStores).catch((e) => setRemoteStores({ error: errorMessage(e) }));
  }, [remote]);

  const set = (k) => (e) => setForm((f) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    // "Copy of": a store of this cluster, or a remote, whose store is then chosen from the list it gives.
    if (k === "copy_of") return { ...f, copy_of: value.startsWith("remote:") ? "" : value, remote: value.startsWith("remote:") ? value.slice(7) : "", remote_store: "" };
    if (k === "remote_store") return { ...f, remote_store: value, copy_of: value ? `${f.remote}:${value}` : "" };
    return { ...f, [k]: value };
  });
  const bad = form ? { name: !NAME_RE.test(form.name), pool: !form.pool, directory: !NAME_RE.test(form.directory),
    master: form.encrypted && form.master_key === "own" && !form.master_public_key.includes("BEGIN PUBLIC KEY"),
    remote: Boolean(form.remote) && !form.remote_store } : {};
  const invalid = Object.values(bad).some(Boolean);

  async function save() {
    setBusy(true);
    try {
      const r = await createBackupStore({
        name: form.name, pool: form.pool, directory: form.directory, encrypted: form.encrypted,
        master_key: form.encrypted && !form.copy_of ? form.master_key : "none", master_public_key: form.master_key === "own" ? form.master_public_key : null,
        key: form.key.trim() || null, copy_of: form.copy_of || null,
      });
      pushToast({ kind: "success", title: t("bst.created"), message: form.name });
      if (r?.key_code || r?.master_private_key) { setSaved(false); setShown({ name: form.name, ...r }); }
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("bst.createFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function cleanUp(s) {
    if (!(await confirmAction({ title: t("bst.cleanTitle", { name: s.name }), message: t("bst.cleanMsg"), confirmLabel: t("bst.clean") }))) return;
    try { await cleanUpBackupStore(s.name); pushToast({ kind: "success", title: t("bst.cleanStarted"), message: s.name }); }
    catch (e) { pushToast({ kind: "error", title: t("bst.cleanFailed"), message: errorMessage(e) }); }
  }
  async function unlock(s) {
    const code = await promptText({ title: t("bst.unlockTitle", { name: s.name }), label: t("bst.keyCode"), confirmLabel: t("bst.unlock"), validate: (v) => (v.trim().length < 20 ? t("bst.keyCodeRule") : "") });
    if (!code) return;
    try { await unlockBackupStore(s.name, code.trim()); pushToast({ kind: "success", title: t("bst.unlocked"), message: s.name }); await load(); }
    catch (e) { pushToast({ kind: "error", title: t("bst.unlockFailed"), message: errorMessage(e) }); }
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
                  <th scope="row">{s.name}{s.encrypted && <> <Chip tone="info">{t("bst.encrypted")}</Chip></>}{s.copy_of && <> <Chip>{t("bst.copyChip", { name: s.copy_of })}</Chip></>}{s.locked && <> <Chip tone="warning">{t("bst.locked")}</Chip></>}{!s.available && <> <Chip tone="danger">{t("bst.unreachable")}</Chip></>}</th>
                  <td className="nx-wrapcell"><span className="nx-mono">{s.pool}/{s.directory}</span>{!s.available && s.problem && <div className="nx-f-h is-error">{s.problem}</div>}</td>
                  <td>{s.available ? <><Meter value={((s.total_bytes - s.free_bytes) / s.total_bytes) * 100} label={`${t("bst.space")} ${s.name}`} /><div className="nx-f-h">{t("bst.free", { free: gb(s.free_bytes), total: gb(s.total_bytes) })}</div></> : "—"}</td>
                  <td className="nx-num nx-mono">{s.available ? s.backups : "—"}</td>
                  <td className="nx-mono">{s.last_gc ? formatDateTime(s.last_gc.at, lang) : <span className="nx-muted">{t("bst.never")}</span>}</td>
                  <td><div className="nx-ra">
                    {s.locked && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("bst.unlockX", { name: s.name })} onClick={() => unlock(s)}>{t("bst.unlock")}</button>}
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
          <Field label={t("bst.copyOf")} hint={t("bst.copyOfHint")}>{(p) => <select {...p} className="nx-inp" value={form.remote ? `remote:${form.remote}` : form.copy_of} onChange={set("copy_of")}>
            <option value="">{t("bst.copyOf.none")}</option>
            {(stores || []).filter((s) => !s.copy_of).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            {remotes.length > 0 && <optgroup label={t("bst.copyOf.remotes")}>{remotes.map((r) => <option key={r.name} value={`remote:${r.name}`}>{t("bst.copyOf.remote", { name: r.name })}</option>)}</optgroup>}
          </select>}</Field>
          {form.remote && <Field label={t("bst.remoteStore")} hint={remoteStores?.error || (remoteStores ? t("bst.remoteStoreHint") : t("bst.remoteStoreLoading"))}>{(p) => <select {...p} className="nx-inp" disabled={!Array.isArray(remoteStores)} value={form.remote_store} onChange={set("remote_store")}>
            <option value="">{t("sec.choose")}</option>{(Array.isArray(remoteStores) ? remoteStores : []).map((s) => <option key={s.name} value={s.name}>{s.name}{s.encrypted ? ` (${t("bst.encrypted")})` : ""}</option>)}
          </select>}</Field>}
          {!form.copy_of && !form.remote && <label className="nx-check"><input type="checkbox" checked={form.encrypted} onChange={set("encrypted")} /> {t("bst.encrypt")}</label>}
          {!form.copy_of && !form.remote && <p className="nx-f-h" style={{ margin: 0 }}>{t("bst.encryptHelp")}</p>}
          {form.encrypted && !form.copy_of && !form.remote && <>
            <Field label={t("bst.master")} hint={t("bst.masterHint")}>{(p) => <select {...p} className="nx-inp" value={form.master_key} onChange={set("master_key")}>
              <option value="none">{t("bst.master.none")}</option><option value="generate">{t("bst.master.generate")}</option><option value="own">{t("bst.master.own")}</option>
            </select>}</Field>
            {form.master_key === "own" && <Field label={t("bst.masterPublic")} error={form.master_public_key && bad.master ? t("bst.masterPublicRule") : null}>{(p) => <textarea {...p} className="nx-inp nx-mono" rows={6} value={form.master_public_key} onChange={set("master_public_key")} placeholder="-----BEGIN PUBLIC KEY-----" />}</Field>}
          </>}
          <Field label={t("bst.takeOverKey")} hint={t("bst.takeOverKeyHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.key} onChange={set("key")} autoComplete="off" />}</Field>
        </>}
      </SideDrawer>
      <SideDrawer open={!!shown} title={t("bst.keysTitle", { name: shown?.name || "" })} onClose={() => saved && setShown(null)} footer={<>
        <button type="button" className="nx-btn nx-btn--primary" disabled={!saved} onClick={() => setShown(null)}>{t("bst.keysDone")}</button>
      </>}>
        {shown && <>
          <div className="nx-bn" data-tone="warning" role="alert"><span className="nx-bn-t">{t("bst.keysWarning")}</span></div>
          {shown.key_code && <>
            <Field label={t("bst.keyCode")} hint={t("bst.keyCodeHint")}>{(p) => <input {...p} className="nx-inp nx-mono" readOnly value={shown.key_code} onFocus={(e) => e.target.select()} />}</Field>
            {/* An SVG the API generated from the key's code (qrcode), as the two-factor setup shows its own. */}
            <div className="nx-qr" style={{ width: 180 }} aria-label={t("bst.keyQr")} role="img" dangerouslySetInnerHTML={{ __html: shown.key_qr_svg }} />
            <button type="button" className="nx-btn" onClick={() => download(`hyperlite-store-${shown.name}.key`, `${shown.key_code}\n`)}>{t("bst.downloadKey")}</button>
          </>}
          {shown.master_private_key && <>
            <p className="nx-f-h" style={{ margin: 0 }}>{t("bst.masterShown")}</p>
            <button type="button" className="nx-btn" onClick={() => download(`hyperlite-store-${shown.name}-master.pem`, shown.master_private_key, "application/x-pem-file")}>{t("bst.downloadMaster")}</button>
          </>}
          <label className="nx-check"><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> {t("bst.keysSaved")}</label>
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
