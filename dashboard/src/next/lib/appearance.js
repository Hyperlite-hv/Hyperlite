import { create } from "zustand";

// How this person wants the console to look and open, kept in this browser like the theme and the language: an
// accent colour, a density, a text size, the page it opens on, and the blocks of Home (their column, order and
// whether they show). Applied as attributes and a variable on <html>, which the style sheets read.
const KEY = "hyperlite-next-appearance";

export const ACCENTS = ["cobalt", "teal", "violet", "forest", "graphite", "crimson"];
export const DENSITIES = ["comfortable", "compact"];
export const SCALES = [0.9, 1, 1.1, 1.2];
// Home's blocks: `main` is the wide column, `side` the narrow one.
export const HOME_BLOCKS = ["setup", "services", "dashboard", "resources", "navigate", "cluster", "watch", "running"];
export const DEFAULT_HOME = {
  main: ["setup", "services", "dashboard", "resources", "navigate"],
  side: ["cluster", "watch", "running"],
  hidden: [],
};
const DEFAULTS = { accent: "cobalt", density: "comfortable", scale: 1, startPage: "summary", home: DEFAULT_HOME };

// A stored layout made whole again: unknown blocks dropped, every known block in exactly one column (a block added in a
// later version lands at the end of its default column).
export function normalizeHome(raw) {
  const known = new Set(HOME_BLOCKS);
  const seen = new Set();
  const take = (list) => (Array.isArray(list) ? list : []).filter((id) => known.has(id) && !seen.has(id) && seen.add(id));
  const main = take(raw?.main);
  const side = take(raw?.side);
  for (const id of DEFAULT_HOME.main) if (!seen.has(id)) { main.push(id); seen.add(id); }
  for (const id of DEFAULT_HOME.side) if (!seen.has(id)) { side.push(id); seen.add(id); }
  const hidden = (Array.isArray(raw?.hidden) ? raw.hidden : []).filter((id) => known.has(id));
  return { main, side, hidden: [...new Set(hidden)] };
}

// A block moved up (-1) or down (+1) in its column, or to the other column (where it goes last).
export function moveBlock(home, id, how) {
  const col = home.main.includes(id) ? "main" : "side";
  const other = col === "main" ? "side" : "main";
  if (how === "switch") return { ...home, [col]: home[col].filter((x) => x !== id), [other]: [...home[other], id] };
  const list = [...home[col]];
  const i = list.indexOf(id);
  const j = i + how;
  if (i < 0 || j < 0 || j >= list.length) return home;
  [list[i], list[j]] = [list[j], list[i]];
  return { ...home, [col]: list };
}

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      accent: ACCENTS.includes(raw.accent) ? raw.accent : DEFAULTS.accent,
      density: DENSITIES.includes(raw.density) ? raw.density : DEFAULTS.density,
      scale: SCALES.includes(raw.scale) ? raw.scale : DEFAULTS.scale,
      startPage: typeof raw.startPage === "string" && /^[a-z]{2,20}$/.test(raw.startPage) ? raw.startPage : DEFAULTS.startPage,
      home: normalizeHome(raw.home),
    };
  } catch { return { ...DEFAULTS, home: normalizeHome(DEFAULT_HOME) }; }
}

export function applyAppearance({ accent, density, scale }) {
  const root = document.documentElement;
  if (accent === "cobalt") delete root.dataset.accent; else root.dataset.accent = accent;
  root.dataset.density = density;
  root.style.setProperty("--ui-scale", String(scale));
}

export const useAppearance = create((set, get) => ({
  ...load(),
  update(patch) {
    set(patch);
    const { accent, density, scale, startPage, home } = get();
    applyAppearance({ accent, density, scale });
    try { localStorage.setItem(KEY, JSON.stringify({ accent, density, scale, startPage, home })); } catch { /* this session only */ }
  },
  setHome(home) { get().update({ home: normalizeHome(home) }); },
  reset() { get().update({ ...DEFAULTS, home: normalizeHome(DEFAULT_HOME) }); },
}));
