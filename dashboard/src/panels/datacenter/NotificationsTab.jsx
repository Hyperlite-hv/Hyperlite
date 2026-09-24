import LoadingState from "../../components/LoadingState";
import { confirmAction } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { Bell, Plus, Trash2, Send, Power, Webhook, Mail } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import {
  fetchNotifyEvents, fetchNotificationChannels, createNotificationChannel,
  setNotificationChannelEnabled, deleteNotificationChannel, testNotificationChannel,
} from "../../api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

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
    if (!(await confirmAction({ title: "Please confirm", message: `Delete the channel '${channel.name}'?`, confirmLabel: "Confirm" }))) return;
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
      <Card className="p-0">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Bell size={15} /> Notification channels
          </h3>
          <Button variant="secondary" onClick={() => setFormOpen((o) => !o)}>
            <Plus /> Add a channel
          </Button>
        </div>

        {formOpen && (
          <form onSubmit={handleCreate} className="space-y-3 border-b border-border px-4 py-4 animate-in fade-in-0 slide-in-from-top-1 duration-150">
            <div className="flex gap-2">
              <Button type="button" variant={form.type === "webhook" ? "default" : "outline"} className={`flex-1 ${form.type === "webhook" ? "" : "text-foreground/80"}`} onClick={() => setForm(EMPTY_WEBHOOK)}>
                <Webhook /> Webhook
              </Button>
              <Button type="button" variant={form.type === "email" ? "default" : "outline"} className={`flex-1 ${form.type === "email" ? "" : "text-foreground/80"}`} onClick={() => setForm(EMPTY_EMAIL)}>
                <Mail /> Email (SMTP)
              </Button>
            </div>

            <div>
              <Label className="text-xs font-medium text-foreground/80">Channel name</Label>
              <Input aria-label="Channel name" className="mt-1" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Discord admin" />
            </div>

            {form.type === "webhook" ? (
              <div>
                <Label className="text-xs font-medium text-foreground/80">Webhook URL</Label>
                <Input aria-label="Webhook URL" className="mt-1" required value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://discord.com/api/webhooks/..." />
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs font-medium text-foreground/80">SMTP server</Label>
                    <Input aria-label="SMTP server" className="mt-1" required value={form.smtp_host} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} placeholder="smtp.example.com" />
                  </div>
                  <div>
                    <Label className="text-xs font-medium text-foreground/80">Port</Label>
                    <Input aria-label="Port" className="mt-1" required value={form.smtp_port} onChange={(e) => setForm({ ...form, smtp_port: e.target.value })} placeholder="587" />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs font-medium text-foreground/80">SMTP user</Label>
                    <Input aria-label="SMTP user" className="mt-1" value={form.smtp_user} onChange={(e) => setForm({ ...form, smtp_user: e.target.value })} />
                  </div>
                  <div>
                    <Label className="text-xs font-medium text-foreground/80">SMTP password</Label>
                    <Input aria-label="SMTP password" className="mt-1" type="password" value={form.smtp_password} onChange={(e) => setForm({ ...form, smtp_password: e.target.value })} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs font-medium text-foreground/80">Sender (From)</Label>
                    <Input aria-label="Sender (From)" className="mt-1" required value={form.from_addr} onChange={(e) => setForm({ ...form, from_addr: e.target.value })} placeholder="hyperlite@example.com" />
                  </div>
                  <div>
                    <Label className="text-xs font-medium text-foreground/80">Recipient (To)</Label>
                    <Input aria-label="Recipient (To)" className="mt-1" required value={form.to_addr} onChange={(e) => setForm({ ...form, to_addr: e.target.value })} placeholder="you@example.com" />
                  </div>
                </div>
              </>
            )}

            <div>
              <Label className="text-xs font-medium text-foreground/80 mb-1.5 block">
                Notified events (no box checked = all)
              </Label>
              <div className="flex flex-wrap gap-2">
                {Object.entries(events).map(([key, label]) => (
                  <button
                    type="button" key={key} onClick={() => toggleEvent(key)}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium border transition-colors duration-150 ${formEvents.includes(key) ? "border-accent-blue bg-accent-blue/10 text-accent-blue" : "border-border text-foreground/80 hover:border-muted-foreground/40"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setFormOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={busy}>{busy ? "Creating..." : "Create"}</Button>
            </div>
          </form>
        )}

        <div className="divide-y divide-border">
          {channels == null && <div className="px-4 py-3 text-sm text-muted-foreground"><LoadingState /></div>}
          {channels && channels.length === 0 && (
            <div className="px-4 py-6 text-sm text-muted-foreground text-center">No channels configured.</div>
          )}
          {channels && channels.map((c) => (
            <div key={c.id} className="flex items-center gap-3 px-4 py-3 text-sm transition-colors duration-150 hover:bg-muted/40">
              {c.type === "webhook" ? <Webhook size={15} className="text-muted-foreground shrink-0" /> : <Mail size={15} className="text-muted-foreground shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="text-foreground font-medium truncate">{c.name}</div>
                <div className="text-muted-foreground text-xs truncate">
                  {c.events.length === 0 ? "All events" : c.events.map((e) => events[e] || e).join(", ")}
                </div>
              </div>
              {!c.enabled && <span className="text-xs text-muted-foreground">disabled</span>}
              <Button variant="secondary" size="sm" onClick={() => handleTest(c)} disabled={testingId === c.id}>
                <Send /> {testingId === c.id ? "..." : "Tester"}
              </Button>
              <Button variant="secondary" size="sm" onClick={() => handleToggle(c)}>
                <Power /> {c.enabled ? "Disable" : "Enable"}
              </Button>
              <Button aria-label={`Delete channel ${c.name}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => handleDelete(c)}>
                <Trash2 size={13} />
              </Button>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
