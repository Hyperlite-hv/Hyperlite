import { create } from "zustand";
import { archiveNotifications, fetchNotificationFeed, markNotificationsRead } from "../../api/client";

// The notification centre's entries for this account (app/core/notification_feed.py), read every 30 seconds by the
// header. `fresh` holds the entries that arrived since the previous read (never on the first one): each is shown once
// as a toast.
export const useFeed = create((set, get) => ({
  items: [],
  unread: 0,
  loaded: false,
  maxId: 0,
  fresh: [],
  async load() {
    try {
      const r = await fetchNotificationFeed();
      const items = Array.isArray(r?.items) ? r.items : [];
      const maxId = items.reduce((m, e) => Math.max(m, e.id), 0);
      const { loaded, maxId: before } = get();
      set({ items, unread: r?.unread ?? 0, loaded: true, maxId: Math.max(before, maxId), fresh: loaded ? items.filter((e) => e.id > before && !e.read) : [] });
    } catch { /* the bell keeps what it had: the next read tries again */ }
  },
  async markRead(ids = null) {
    const optimistic = (e) => (ids == null || ids.includes(e.id) ? { ...e, read: true } : e);
    set((s) => ({ items: s.items.map(optimistic), unread: ids == null ? 0 : Math.max(0, s.unread - s.items.filter((e) => ids.includes(e.id) && !e.read).length) }));
    try { const r = await markNotificationsRead(ids); set({ unread: r.unread }); } catch { get().load(); }
  },
  async archive(ids) {
    set((s) => ({ items: s.items.filter((e) => !ids.includes(e.id)) }));
    try { const r = await archiveNotifications(ids); set({ unread: r.unread }); } catch { get().load(); }
  },
}));

// "type:id:tab" (or "datacenter::tab") → the navigateTo arguments.
export function parseLink(link) {
  if (!link) return null;
  const [type, id, tab] = link.split(":");
  return { type, id: id || null, tab: tab || "summary" };
}
