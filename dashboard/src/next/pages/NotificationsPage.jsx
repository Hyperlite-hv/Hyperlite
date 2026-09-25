import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchNotifyEvents, fetchNotificationChannels, createNotificationChannel,
  setNotificationChannelEnabled, deleteNotificationChannel, testNotificationChannel,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import { EmptyState, ErrorState } from "../components/States";

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
  const err = (k) => touched && problems[k] && <span id={`nt-${k}`} className="nx-hint nx-hint--error">{t(problems[k])}</span>;
  const inv = (k) => ({ "aria-invalid": touched && problems[k] ? true : undefined, "aria-describedby": touched && problems[k] ? `nt-${k}` : undefined });
  const reset = () => { setForm(EMPTY_WEBHOOK); setPicked([]); setTouched(false); };

  async function create(e) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(problems).length) return;
    setBusy(true);
    try {
      const config = form.type === "webhook" ? { url: form.url }
        : { smtp_host: form.smtp_host, smtp_port: form.smtp_port, smtp_user: form.smtp_user, smtp_password: form.smtp_password, from_addr: form.from_addr, to_addr: form.to_addr, use_tls: form.use_tls };
      await createNotificationChannel({ type: form.type, name: form.name.trim(), config, events: picked });
      pushToast({ kind: "success", title: t("nt.created"), message: form.name });
      setOpen(false); reset(); reload();
    } catch (er) { pushToast({ kind: "error", title: t("nt.createFailed"), message: errorMessage(er) }); }
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

  if (error && channels == null) return <ErrorState message={error} onRetry={reload} />;
  const list = channels || [];

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="nt-title">
        <div className="nx-cardhead">
          <h2 id="nt-title">{t("nt.title")} <span className="nx-count">{channels ? list.length : "…"}</span></h2>
          <button type="button" className="nx-btn nx-btn--primary" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{t("nt.add")}</button>
        </div>
        <p className="nx-muted" style={{ marginTop: 0, maxWidth: "62ch" }}>{t("nt.intro")}</p>

        {open && (
          <form className="nx-form" onSubmit={create} noValidate>
            <div className="nx-seg nx-seg--wide" role="group" aria-label={t("nt.type")}>
              <button type="button" aria-pressed={form.type === "webhook"} onClick={() => { setForm(EMPTY_WEBHOOK); setTouched(false); }}>Webhook</button>
              <button type="button" aria-pressed={form.type === "email"} onClick={() => { setForm(EMPTY_EMAIL); setTouched(false); }}>{t("nt.email")}</button>
            </div>
            <label>{t("nt.name")}<input className="nx-input" aria-label="Channel name" required value={form.name} onChange={set("name")} placeholder="Discord admin" {...inv("name")} />{err("name")}</label>
            {form.type === "webhook" ? (
              <label>Webhook URL<input className="nx-input" aria-label="Webhook URL" required inputMode="url" value={form.url} onChange={set("url")} placeholder="https://discord.com/api/webhooks/…" {...inv("url")} />{err("url")}</label>
            ) : (
              <>
                <div className="nx-formgrid">
                  <label>{t("nt.smtpHost")}<input className="nx-input" aria-label="SMTP server" required value={form.smtp_host} onChange={set("smtp_host")} placeholder="smtp.example.com" {...inv("smtp_host")} />{err("smtp_host")}</label>
                  <label>Port<input className="nx-input" aria-label="Port" required inputMode="numeric" value={form.smtp_port} onChange={set("smtp_port")} {...inv("smtp_port")} />{err("smtp_port")}</label>
                  <label>{t("nt.smtpUser")}<input className="nx-input" aria-label="SMTP user" autoComplete="off" value={form.smtp_user} onChange={set("smtp_user")} /></label>
                  <label>{t("nt.smtpPassword")}<input className="nx-input" aria-label="SMTP password" type="password" autoComplete="new-password" value={form.smtp_password} onChange={set("smtp_password")} /><span className="nx-hint">{t("nt.secretHelp")}</span></label>
                  <label>{t("nt.from")}<input className="nx-input" aria-label="Sender (From)" required inputMode="email" value={form.from_addr} onChange={set("from_addr")} placeholder="hyperlite@example.com" {...inv("from_addr")} />{err("from_addr")}</label>
                  <label>{t("nt.to")}<input className="nx-input" aria-label="Recipient (To)" required inputMode="email" value={form.to_addr} onChange={set("to_addr")} placeholder="you@example.com" {...inv("to_addr")} />{err("to_addr")}</label>
                </div>
                <label className="nx-check"><input type="checkbox" checked={form.use_tls} onChange={(e) => setForm((f) => ({ ...f, use_tls: e.target.checked }))} /> {t("nt.tls")}</label>
              </>
            )}
            <fieldset className="nx-fieldset">
              <legend>{t("nt.events")}</legend>
              <span className="nx-hint">{t("nt.eventsHelp")}</span>
              <div className="nx-checks">
                {Object.entries(events).map(([k, label]) => <label key={k} className="nx-check"><input type="checkbox" checked={picked.includes(k)} onChange={() => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]))} /> {label}</label>)}
              </div>
            </fieldset>
            <div className="nx-formactions">
              <button type="button" className="nx-btn" onClick={() => { setOpen(false); reset(); }}>{t("action.cancel")}</button>
              <button type="submit" className="nx-btn nx-btn--primary" disabled={busy}>{busy ? t("stor.creating") : t("action.create")}</button>
            </div>
          </form>
        )}

        {channels == null ? <p className="nx-muted" role="status">{t("loading")}</p> : list.length === 0 ? (
          <EmptyState title={t("nt.none")} help={t("nt.noneHelp")} />
        ) : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("nt.name")}</th><th scope="col">{t("nt.type")}</th><th scope="col">{t("nt.events")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.id}>
                    <td><StatusIndicator override={c.enabled ? ON : OFF} /></td>
                    <th scope="row">{c.name}</th>
                    <td>{c.type === "email" ? t("nt.email") : "Webhook"}</td>
                    <td>{c.events.length === 0 ? t("nt.allEvents") : c.events.map((k) => events[k] || k).join(", ")}</td>
                    <td className="nx-num nx-rowactions">
                      <button type="button" className="nx-btn" disabled={testing === c.id} aria-label={`Test ${c.name}`} onClick={() => test(c)}>{testing === c.id ? "…" : t("nt.test")}</button>
                      <button type="button" className="nx-btn" aria-label={`${c.enabled ? "Disable" : "Enable"} ${c.name}`} onClick={() => toggle(c)}>{c.enabled ? t("nt.disable") : t("nt.enable")}</button>
                      <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete channel ${c.name}`} onClick={() => remove(c)}>{t("menu.delete").replace("…", "")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
