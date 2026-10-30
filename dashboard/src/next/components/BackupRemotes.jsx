import { useCallback, useEffect, useState } from "react";
import { Globe, Plus, Trash2 } from "lucide-react";
import { fetchBackupRemotes, probeBackupRemote, createBackupRemote, deleteBackupRemote, fetchThisNodeFingerprint } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "./States";
import { Card, Empty, Field, Loading, SideDrawer, TableWrap } from "./ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$/;
const EMPTY = { name: "", address: "", port: "8000", fingerprint: "", matches: false, token: "" };
const groups = (hex) => (hex || "").match(/.{1,4}/g)?.join(" ") || "";

// Other Hyperlite clusters whose backup stores this one copies, as Proxmox Backup Server's remotes: an address, its
// HTTPS certificate pinned once an administrator compared it with the one the other dashboard shows, and the API
// token of a dedicated account there that may only read its stores (the role "Backup store reader").
export default function BackupRemotes({ onChange }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [remotes, setRemotes] = useState(null);
  const [own, setOwn] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const r = await fetchBackupRemotes(); setRemotes(r); setError(null); onChange?.(r); } catch (e) { setError(errorMessage(e)); }
  }, [onChange]);
  useEffect(() => { load(); fetchThisNodeFingerprint().then((r) => setOwn(r.fingerprint)).catch(() => setOwn(null)); }, [load]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value,
    ...(k === "address" || k === "port" ? { fingerprint: "", matches: false } : {}) }));
  const port = Number(form?.port);
  const ready = form && NAME_RE.test(form.name) && form.address && Number.isInteger(port) && form.fingerprint && form.matches && form.token;

  async function probe() {
    setBusy(true);
    try { const r = await probeBackupRemote(form.address.trim(), port); setForm((f) => ({ ...f, fingerprint: r.fingerprint, matches: false })); }
    catch (e) { pushToast({ kind: "error", title: t("brm.probeFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function save() {
    setBusy(true);
    try {
      await createBackupRemote({ name: form.name, address: form.address.trim(), port, fingerprint: form.fingerprint, token: form.token.trim() });
      pushToast({ kind: "success", title: t("brm.saved"), message: form.name });
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("brm.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function remove(r) {
    if (!(await confirmAction({ title: t("brm.deleteTitle", { name: r.name }), message: t("brm.deleteMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteBackupRemote(r.name); await load(); } catch (e) { pushToast({ kind: "error", title: t("brm.deleteFailed"), message: errorMessage(e) }); }
  }

  return (
    <>
      <Card title={t("brm.title")} flush actions={<button type="button" className="nx-btn" onClick={() => setForm({ ...EMPTY })}><Plus size={15} aria-hidden="true" />{t("brm.add")}</button>}>
        {own && <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("brm.ownHelp")} <code className="nx-mono" aria-label={t("brm.ownFingerprint")}>{groups(own)}</code></p>}
        {error && !remotes ? <ErrorState message={error} onRetry={load} /> : !remotes ? <Loading style={{ padding: "var(--space-4)" }} /> : remotes.length === 0 ? <Empty icon={Globe} title={t("brm.none")} text={t("brm.noneHelp")} /> : (
          <TableWrap label={t("brm.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("bg.name")}</th><th scope="col">{t("brm.address")}</th><th scope="col">{t("brm.fingerprint")}</th><th scope="col">{t("vm.created")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>{remotes.map((r) => (
                <tr key={r.name}>
                  <th scope="row">{r.name}</th>
                  <td className="nx-mono">{r.address}:{r.port}</td>
                  <td className="nx-mono nx-wrapcell" title={r.fingerprint}>{groups(r.fingerprint).slice(0, 24)}…</td>
                  <td className="nx-mono">{formatDateTime(r.created_at, lang)}</td>
                  <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("mx.deleteX", { name: r.name })} onClick={() => remove(r)}><Trash2 size={15} aria-hidden="true" /></button></div></td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <SideDrawer open={!!form} title={t("brm.add")} onClose={() => setForm(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setForm(null)}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={!ready || busy} onClick={save}>{t("mx.save")}</button>
      </>}>
        {form && <>
          <p className="nx-muted" style={{ margin: 0 }}>{t("brm.help")}</p>
          <Field label={t("bg.name")} error={form.name && !NAME_RE.test(form.name) ? t("bst.nameRule") : null}>{(p) => <input {...p} className="nx-inp" value={form.name} onChange={set("name")} placeholder="site-a" />}</Field>
          <div className="nx-fg">
            <Field label={t("brm.address")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.address} onChange={set("address")} placeholder="203.0.113.10" />}</Field>
            <Field label={t("brm.port")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={1} max={65535} value={form.port} onChange={set("port")} />}</Field>
          </div>
          <button type="button" className="nx-btn" disabled={busy || !form.address || !Number.isInteger(port)} onClick={probe}>{t("brm.probe")}</button>
          {form.fingerprint && <>
            <Field label={t("brm.fingerprint")} hint={t("brm.fingerprintHint")}>{(p) => <input {...p} className="nx-inp nx-mono" readOnly value={groups(form.fingerprint)} />}</Field>
            <label className="nx-check"><input type="checkbox" checked={form.matches} onChange={set("matches")} /> {t("brm.matches")}</label>
          </>}
          <Field label={t("brm.token")} hint={t("brm.tokenHint")}>{(p) => <input {...p} className="nx-inp nx-mono" type="password" autoComplete="off" value={form.token} onChange={set("token")} />}</Field>
        </>}
      </SideDrawer>
    </>
  );
}
