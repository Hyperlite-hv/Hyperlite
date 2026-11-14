import { create } from "zustand";
import { createDashboard, fetchDashboards, updateDashboard } from "../../api/client";

// The dashboards this account reads (app/core/dashboards.py), and the one Home shows (kept in this browser).
const HOME_KEY = "hl.home.dashboard";
const MOVED_KEY = "hl.home.pinsMoved";
const read = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* this page only */ } };
export const newTileId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export const useDashboards = create((set, get) => ({
  list: null,
  home: read(HOME_KEY),
  async load() {
    const list = await fetchDashboards().catch(() => []);
    set({ list: Array.isArray(list) ? list : [] });
    return get().list;
  },
  setHome(id) { write(HOME_KEY, id); set({ home: id }); },
  // Replace one dashboard in the list after a change.
  put(d) { set({ list: [...(get().list || []).filter((x) => x.id !== d.id), d].sort((a, b) => a.name.localeCompare(b.name)) }); },
  drop(id) { set({ list: (get().list || []).filter((x) => x.id !== id) }); if (get().home === id) get().setHome(null); },
  // Add a tile to a dashboard (by id), or to a new one named `newName`.
  async pin(tile, { id, newName }) {
    const t = { ...tile, id: newTileId() };
    const d = id
      ? await updateDashboard(id, { tiles: [...((get().list || []).find((x) => x.id === id)?.tiles || []), t] })
      : await createDashboard({ name: newName, tiles: [t] });
    get().put(d);
    return d;
  },
}));

// The charts pinned on Home in this browser before dashboards existed go, once, to a first private dashboard.
export async function moveOldPins(pins, name) {
  if (read(MOVED_KEY) || !pins?.length) return null;
  const tiles = pins.map((p) => ({ id: newTileId(), type: "chart", title: p.title, size: 1, config: { series: p.series, range: p.range } }));
  const d = await createDashboard({ name, tiles });
  write(MOVED_KEY, "1");
  useDashboards.getState().put(d);
  if (!useDashboards.getState().home) useDashboards.getState().setHome(d.id);
  return d;
}
