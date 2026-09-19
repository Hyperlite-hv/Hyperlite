import { useCallback, useEffect, useState } from "react";
import { Bell, Plus, Trash2, Send, Power, Webhook, Mail } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import {
  fetchNotifyEvents, fetchNotificationChannels, createNotificationChannel,
  setNotificationChannelEnabled, deleteNotificationChannel, testNotificationChannel,
} from "../../api/client";

const EMPTY_WEBHOOK = { type: "webhook", name: "", url: "" };
const EMPTY_EMAIL = {
  type: "email", name: "", smtp_host: "", smtp_port: "587", smtp_user: "", smtp_password: "",
  from_addr: "", to_addr: "", use_tls: true,
};

// Outgoing notification channels (generic webhook or email/SMTP): managed here,
// triggered automatically by the backend (see app/core/audit.py::log_action, the
// single entry point) for the events listed below. Nothing needs to be configured
// event by event beyond the per-channel checkbox.
export default function NotificationsTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [events, setEvents] = useState({});
  const [channels, setChannels] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_WEBHOOK);
  const [formEvents, setFormEvents] = useState([]);
  const [busy, setBusy] = useState(false);
  const [testingId, setTestingId] = useState(null);

  const reload = useCallback(async () => {
    try { setChannels(await fetchNotificationChannels()); }
    catch (e) { pushToast({ kind: "error", title: "Error", message: e.message }); }
  }, [pushToast]);

  useEffect(() => {
    fetchNotifyEvents().then(setEvents).catch(() => {});
    reload();
  }, [reload]);

  function toggleEvent(key) {
    setFormEvents((prev) => (prev.includes(key) ? prev.filter((e) => e !== key) : [...prev, key]));
  }

  async function handleCreate(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const config = form.type === "webhook"
        ? { url: form.url }
        : {
            smtp_host: form.smtp_host, smtp_port: form.smtp_port, smtp_user: form.smtp_user,
            smtp_password: form.smtp_password, from_addr: form.from_addr, to_addr: form.to_addr,
            use_tls: form.use_tls,
          };
      await createNotificationChannel({ type: form.type, name: form.name, config, events: formEvents });
      pushToast({ kind: "success", title: "Channel created", message: form.name });
      setFormOpen(false);
      setForm(EMPTY_WEBHOOK);
      setFormEvents([]);
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleToggle(channel) {
    try {
      await setNotificationChannelEnabled(channel.id, !channel.enabled);
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  async function handleDelete(channel) {
    if (!window.confirm(`Delete the channel '${channel.name}'?`)) return;
    try {
      await deleteNotificationChannel(channel.id);
      pushToast({ kind: "success", title: "Channel deleted", message: channel.name });
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  async function handleTest(channel) {
    setTestingId(channel.id);
    try {
      await testNotificationChannel(channel.id);
      pushToast({ kind: "success", title: "Test sent", message: `Check ${channel.type === "email" ? "the mailbox" : "the webhook receiver"}` });
    } catch (e) {
      pushToast({ kind: "error", title: "Test failed", message: e.message });
    } finally {
      setTestingId(null);
    }
  }

  return (
    <div className="space-y-5">
      <div className="card">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-anthracite-600">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-anthracite-100">
            <Bell size={15} /> Notification channels
          </h3>
          <button className="btn-secondary" onClick={() => setFormOpen((o) => !o)}>
            <Plus size={14} /> Add a channel
          </button>
        </div>

        {formOpen && (
          <form onSubmit={handleCreate} className="space-y-3 border-b border-anthracite-600 px-4 py-4">
            <div className="flex gap-2">
              <button type="button" onClick={() => setForm(EMPTY_WEBHOOK)}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium ${form.type === "webhook" ? "border-accent-blue bg-accent-blue/10 text-accent-blue" : "border-anthracite-600 text-anthracite-300"}`}>
                <Webhook size={14} /> Webhook
              </button>
              <button type="button" onClick={() => setForm(EMPTY_EMAIL)}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium ${form.type === "email" ? "border-accent-blue bg-accent-blue/10 text-accent-blue" : "border-anthracite-600 text-anthracite-300"}`}>
                <Mail size={14} /> Email (SMTP)
              </button>
            </div>

            <div>
              <label className="text-xs font-medium text-anthracite-300">Channel name</label>
              <input className="input mt-1" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Discord admin" />
            </div>

            {form.type === "webhook" ? (
              <div>
                <label className="text-xs font-medium text-anthracite-300">Webhook URL</label>
                <input className="input mt-1" required value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://discord.com/api/webhooks/..." />
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-anthracite-300">SMTP server</label>
                    <input className="input mt-1" required value={form.smtp_host} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} placeholder="smtp.example.com" />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-anthracite-300">Port</label>
                    <input className="input mt-1" required value={form.smtp_port} onChange={(e) => setForm({ ...form, smtp_port: e.target.value })} placeholder="587" />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-anthracite-300">SMTP user</label>
                    <input className="input mt-1" value={form.smtp_user} onChange={(e) => setForm({ ...form, smtp_user: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-anthracite-300">SMTP password</label>
                    <input className="input mt-1" type="password" value={form.smtp_password} onChange={(e) => setForm({ ...form, smtp_password: e.target.value })} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-anthracite-300">Sender (From)</label>
                    <input className="input mt-1" required value={form.from_addr} onChange={(e) => setForm({ ...form, from_addr: e.target.value })} placeholder="hyperlite@example.com" />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-anthracite-300">Recipient (To)</label>
                    <input className="input mt-1" required value={form.to_addr} onChange={(e) => setForm({ ...form, to_addr: e.target.value })} placeholder="you@example.com" />
                  </div>
                </div>
              </>
            )}

            <div>
              <label className="text-xs font-medium text-anthracite-300 mb-1.5 block">
                Notified events (no box checked = all)
              </label>
              <div className="flex flex-wrap gap-2">
                {Object.entries(events).map(([key, label]) => (
                  <button
                    type="button" key={key} onClick={() => toggleEvent(key)}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium border ${formEvents.includes(key) ? "border-accent-blue bg-accent-blue/10 text-accent-blue" : "border-anthracite-600 text-anthracite-300"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>Cancel</button>
              <button type="submit" disabled={busy} className="btn-primary">{busy ? "Creating..." : "Create"}</button>
            </div>
          </form>
        )}

        <div className="divide-y divide-anthracite-600">
          {channels == null && <div className="px-4 py-3 text-sm text-anthracite-400">Loading...</div>}
          {channels && channels.length === 0 && (
            <div className="px-4 py-6 text-sm text-anthracite-400 text-center">No channels configured.</div>
          )}
          {channels && channels.map((c) => (
            <div key={c.id} className="flex items-center gap-3 px-4 py-3 text-sm">
              {c.type === "webhook" ? <Webhook size={15} className="text-anthracite-400 shrink-0" /> : <Mail size={15} className="text-anthracite-400 shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="text-anthracite-100 font-medium truncate">{c.name}</div>
                <div className="text-anthracite-400 text-xs truncate">
                  {c.events.length === 0 ? "All events" : c.events.map((e) => events[e] || e).join(", ")}
                </div>
              </div>
              {!c.enabled && <span className="text-xs text-anthracite-500">disabled</span>}
              <button className="btn-secondary !py-1" onClick={() => handleTest(c)} disabled={testingId === c.id}>
                <Send size={13} /> {testingId === c.id ? "..." : "Tester"}
              </button>
              <button className="btn-secondary !py-1" onClick={() => handleToggle(c)}>
                <Power size={13} /> {c.enabled ? "Disable" : "Enable"}
              </button>
              <button aria-label="Delete" className="btn-danger !py-1" onClick={() => handleDelete(c)}>
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
