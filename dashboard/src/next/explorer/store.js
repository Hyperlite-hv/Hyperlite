import { create } from "zustand";

const KEY = "hyperlite-next-explorer";

function load() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      mode: v.mode === "pool" ? "pool" : "server",
      toggled: v.toggled && typeof v.toggled === "object" ? v.toggled : {},
      favorites: Array.isArray(v.favorites) ? v.favorites : [],
      recents: Array.isArray(v.recents) ? v.recents : [],
      density: v.density === "compact" ? "compact" : "comfortable",
    };
  } catch {
    return { mode: "server", toggled: {}, favorites: [], recents: [], density: "comfortable" };
  }
}

function save(s) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ mode: s.mode, toggled: s.toggled, favorites: s.favorites, recents: s.recents, density: s.density }));
  } catch { /* storage unavailable: preferences last for this session only */ }
}

// Only per-user UI preferences are persisted here (never data, never secrets).
export const useExplorerStore = create((set, get) => ({
  ...load(),
  query: "",
  setQuery(query) { set({ query }); },
  setMode(mode) { set({ mode }); save(get()); },
  setOpen(key, open) { set((s) => ({ toggled: { ...s.toggled, [key]: open } })); save(get()); },
  toggleFavorite(id) {
    set((s) => ({ favorites: s.favorites.includes(id) ? s.favorites.filter((f) => f !== id) : [...s.favorites, id] }));
    save(get());
  },
  pushRecent(id) {
    set((s) => ({ recents: [id, ...s.recents.filter((r) => r !== id)].slice(0, 8) }));
    save(get());
  },
  setDensity(density) { set({ density }); save(get()); },
}));
