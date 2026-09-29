import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchNotifyEvents, fetchNotificationChannels, createNotificationChannel, updateNotificationChannel,
  setNotificationChannelEnabled, deleteNotificationChannel, testNotificationChannel,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import { ErrorState } from "../components/States";
import { PageHeader, Empty, SideDrawer, Field, Loading, TableWrap } from "../components/ui";
import { Bell, Mail, Pencil, Plus, Trash2, Webhook } from "lucide-react";

const EMPTY_WEBHOOK = { type: "webhook", name: "", url: "" };
const EMPTY_EMAIL = { type: "email", name: "", smtp_host: "", smtp_port: "587", smtp_user: "", smtp_password: "", from_addr: "", to_addr: "", use_tls: true };
const ON = { key: "state.active", shape: "dot", tone: "success" };
const OFF = { key: "state.inactive", shape: "square", tone: "offline" };
const isHttp = (v) => { try { return /^https?:$/.test(new URL(v).protocol); } catch { return false; } };
const isMail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

// Outgoing channels (webhook or SMTP). The backend fires them from its single audit entry point; a channel
// with no event selected receives everything. Secrets are stored encrypted and never returned.
export default function NotificationsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [events, setEvents] = useState({});
  const [channels, setChannels] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null); // the channel being edited, null when creating one
  const [form, setForm] = useState(EMPTY_WEBHOOK);
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(null);
  const [touched, setTouched] = useState(false);

  const reload = useCallback(async () => {
    try { const c = await fetchNotificationChannels(); setChannels(Array.isArray(c) ? c : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { fetchNotifyEvents().then(setEvents).catch(() => {}); reload(); }, [reload]);

  const problems = useMemo(() => {
    const p = {};
    if (!form.name.trim()) p.name = "nt.required";
    if (form.type === "webhook") {
      if (!form.url) p.url = "nt.required"; else if (!isHttp(form.url)) p.url = "nt.badUrl";
    } else {
      if (!form.smtp_host.trim()) p.smtp_host = "nt.required";
      const port = Number(form.smtp_port);
      if (!Number.isInteger(port) || port < 1 || port > 65535) p.smtp_port = "nt.badPort";
      if (!isMail(form.from_addr)) p.from_addr = "nt.badMail";
      if (!isMail(form.to_addr)) p.to_addr = "nt.badMail";
    }
    return p;
  }, [form]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const reset = () => { setForm(EMPTY_WEBHOOK); setPicked([]); setTouched(false); setEditing(null); };

  async function submit(e) {
    e?.preventDefault();
    setTouched(true);
    if (Object.keys(problems).length) return;
    setBusy(true);
    try {
      const fields = form.type === "webhook" ? { url: form.url }
        : { smtp_host: form.smtp_host, smtp_port: form.smtp_port, smtp_user: form.smtp_user, smtp_password: form.smtp_password, from_addr: form.from_addr, to_addr: form.to_addr, use_tls: form.use_tls };
      if (editing) {
        // Settings this form does not show are kept; the masked password markers are not settings.
        const kept = Object.fromEntries(Object.entries(editing.config || {}).filter(([k]) => k !== "smtp_password" && k !== "smtp_password_set"));
        await updateNotificationChannel(editing.id, { name: form.name.trim(), config: { ...kept, ...fields }, events: picked });
        pushToast({ kind: "success", title: t("nt.updated"), message: form.name });
      } else {
        await createNotificationChannel({ type: form.type, name: form.name.trim(), config: fields, events: picked });
        pushToast({ kind: "success", title: t("nt.created"), message: form.name });
      }
      setOpen(false); reset(); reload();
    } catch (er) { pushToast({ kind: "error", title: t(editing ? "nt.updateFailed" : "nt.createFailed"), message: errorMessage(er) }); }
    finally { setBusy(false); }
  }
  async function toggle(c) {
    try { await setNotificationChannelEnabled(c.id, !c.enabled); reload(); }
    catch (er) { pushToast({ kind: "error", title: t("action.failed", { action: c.enabled ? t("nt.disable") : t("nt.enable") }), message: errorMessage(er) }); }
  }
  async function remove(c) {
    if (!(await confirmAction({ title: t("nt.deleteTitle", { name: c.name }), message: t("nt.deleteMsg"), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    try { await deleteNotificationChannel(c.id); pushToast({ kind: "success", title: t("nt.deleted"), message: c.name }); reload(); }
    catch (er) { pushToast({ kind: "error", title: t("nt.deleteFailed"), message: errorMessage(er) }); }
  }
  async function test(c) {
    setTesting(c.id);
    try { await testNotificationChannel(c.id); pushToast({ kind: "success", title: t("nt.testSent"), message: c.type === "email" ? t("nt.checkMail") : t("nt.checkHook") }); }
    catch (er) { pushToast({ kind: "error", title: t("nt.testFailed"), message: errorMessage(er) }); }
    finally { setTesting(null); }
  }

  const list = channels || [];
  const openWith = (type) => { setForm(type === "email" ? EMPTY_EMAIL : EMPTY_WEBHOOK); setPicked([]); setEditing(null); setTouched(false); setOpen(true); };
  const openEdit = (c) => {
    const cfg = c.config || {};
    setForm(c.type === "email"
      ? { ...EMPTY_EMAIL, name: c.name, smtp_host: cfg.smtp_host || "", smtp_port: String(cfg.smtp_port ?? "587"), smtp_user: cfg.smtp_user || "", smtp_password: "", from_addr: cfg.from_addr || "", to_addr: cfg.to_addr || "", use_tls: cfg.use_tls ?? true }
      : { ...EMPTY_WEBHOOK, name: c.name, url: cfg.url || "" });
    setPicked(Array.isArray(c.events) ? c.events : []);
    setEditing(c); setTouched(false); setOpen(true);
  };
  const close = () => { setOpen(false); reset(); };
  const input = (k, label, aria, extra = {}) => (
    <Field label={label} error={touched && problems[k] ? t(problems[k]) : null} hint={extra.hint}>
      {(p) => <input {...p} className={`nx-inp${extra.mono ? " nx-mono" : ""}`} aria-label={aria} value={form[k]} onChange={set(k)} {...extra.input} />}
    </Field>
  );

  return (
    <>
      <PageHeader title={t("tab.notifications")} count={channels ? list.length : null} desc={t("nt.desc")}
        actions={<button type="button" className="nx-btn nx-btn--primary" onClick={() => openWith("webhook")}><Plus size={15} aria-hidden="true" />{t("nt.add")}</button>} />
      {error && channels == null ? <ErrorState message={error} onRetry={reload} /> : (
        <div className="nx-card2 nx-card2--flush">
          {channels == null ? <Loading style={{ padding: "var(--space-4)" }} /> : list.length === 0 ? (
            <Empty icon={Bell} title={t("nt.none")} text={t("nt.noneHelp")} action={<div className="nx-inline">
              <button type="button" className="nx-btn" onClick={() => openWith("webhook")}><Webhook size={15} aria-hidden="true" />Webhook</button>
              <button type="button" className="nx-btn" onClick={() => openWith("email")}><Mail size={15} aria-hidden="true" />{t("nt.email")}</button>
            </div>} />
          ) : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("nt.name")}</th><th scope="col">{t("nt.type")}</th><th scope="col">{t("nt.events")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {list.map((c) => (
                    <tr key={c.id}>
                      <td><StatusIndicator override={c.enabled ? ON : OFF} /></td>
                      <th scope="row">{c.name}</th>
                      <td>{c.type === "email" ? t("nt.email") : "Webhook"}</td>
                      <td className="nx-wrapcell">{c.events.length === 0 ? t("nt.allEvents") : c.events.map((k) => events[k] || k).join(", ")}</td>
                      <td><div className="nx-ra">
                        <button type="button" className="nx-btn nx-btn--sm" disabled={testing === c.id} aria-label={t("a11y.test_x", { v: c.name })} onClick={() => test(c)}>{testing === c.id ? "…" : t("nt.test")}</button>
                        {!c.config?.redacted && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("nt.editAria", { v: c.name })} onClick={() => openEdit(c)}><Pencil size={14} aria-hidden="true" />{t("nt.edit")}</button>}
                        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t(c.enabled ? "a11y.disable_x" : "a11y.enable_x", { v: c.name })} onClick={() => toggle(c)}>{c.enabled ? t("nt.disable") : t("nt.enable")}</button>
                        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_channel_x", { v: c.name })} title={t("menu.delete").replace("…", "")} onClick={() => remove(c)}><Trash2 size={15} aria-hidden="true" /></button>
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </div>
      )}
      <SideDrawer open={open} title={editing ? t("nt.editTitle", { name: editing.name }) : t("nt.add")} onClose={close} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={close} disabled={busy}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={submit}>{editing ? (busy ? t("nt.saving") : t("nt.save")) : (busy ? t("stor.creating") : t("nt.addBtn"))}</button>
      </>}>
        {/* The type of an existing channel cannot change: delete it and create another. */}
        {!editing && <div className="nx-seg2" role="group" aria-label={t("nt.type")}>
          <button type="button" aria-pressed={form.type === "webhook"} onClick={() => { setForm(EMPTY_WEBHOOK); setTouched(false); }}>Webhook</button>
          <button type="button" aria-pressed={form.type === "email"} onClick={() => { setForm(EMPTY_EMAIL); setTouched(false); }}>{t("nt.email")}</button>
        </div>}
        {input("name", t("nt.name"), t("a11y.channel_name"), { input: { placeholder: "Discord admin" } })}
        {form.type === "webhook" ? input("url", t("nt.webhookUrl"), t("nt.webhookUrl"), { mono: true, input: { inputMode: "url", placeholder: "https://discord.com/api/webhooks/…" } }) : (
          <>
            <div className="nx-fg">
              {input("smtp_host", t("nt.smtpHost"), t("a11y.smtp_server"), { mono: true, input: { placeholder: "smtp.example.com" } })}
              {input("smtp_port", t("a11y.smtp_port"), t("a11y.smtp_port"), { mono: true, input: { inputMode: "numeric" } })}
              {input("smtp_user", t("nt.smtpUser"), t("a11y.smtp_user"), { input: { autoComplete: "off" } })}
              {input("smtp_password", t("nt.smtpPassword"), t("a11y.smtp_password"), { hint: editing?.config?.smtp_password_set ? t("nt.keepPassword") : t("nt.secretHelp"), input: { type: "password", autoComplete: "new-password" } })}
              {input("from_addr", t("nt.from"), t("a11y.sender_from"), { input: { inputMode: "email", placeholder: "hyperlite@example.com" } })}
              {input("to_addr", t("nt.to"), t("a11y.recipient_to"), { input: { inputMode: "email", placeholder: "you@example.com" } })}
            </div>
            <label className="nx-check"><input type="checkbox" checked={form.use_tls} onChange={(e) => setForm((f) => ({ ...f, use_tls: e.target.checked }))} /> {t("nt.tls")}</label>
          </>
        )}
        <fieldset className="nx-fs">
          <legend>{t("nt.events")}</legend>
          <p className="nx-f-h" style={{ margin: "0 0 var(--space-2)" }}>{t("nt.eventsHelp")}</p>
          <div className="nx-checks">
            {Object.entries(events).map(([k, label]) => <label key={k} className="nx-check"><input type="checkbox" checked={picked.includes(k)} onChange={() => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]))} /> {t(`nt.ev.${k}`) === `nt.ev.${k}` ? label : t(`nt.ev.${k}`)}</label>)}
          </div>
        </fieldset>
      </SideDrawer>
    </>
  );
}
NotificationsPage.ownHeader = true;
