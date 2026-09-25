import { create } from "zustand";

const KEY = "hyperlite-next-theme";
const MODES = ["dark", "light", "system"];

function readStored() {
  try {
    const v = localStorage.getItem(KEY);
    return MODES.includes(v) ? v : "dark";
  } catch {
    return "dark";
  }
}

export function resolveTheme(mode, prefersDark) {
  if (mode === "system") return prefersDark ? "dark" : "light";
  return mode;
}

function systemPrefersDark() {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

// The `dark` class is also toggled on <html> so shared portal components of the
// legacy design system (dialogs opened by not-yet-migrated panels) follow the theme.
function apply(mode) {
  const resolved = resolveTheme(mode, systemPrefersDark());
  const root = document.documentElement;
  root.dataset.theme = resolved;
  root.classList.toggle("dark", resolved === "dark");
  return resolved;
}

export const useThemeStore = create((set, get) => ({
  mode: readStored(),
  resolved: "dark",
  init() {
    set({ resolved: apply(get().mode) });
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onChange = () => { if (get().mode === "system") set({ resolved: apply("system") }); };
    mq?.addEventListener?.("change", onChange);
    return () => mq?.removeEventListener?.("change", onChange);
  },
  setMode(mode) {
    if (!MODES.includes(mode)) return;
    try { localStorage.setItem(KEY, mode); } catch { /* storage unavailable: applies for this session only */ }
    set({ mode, resolved: apply(mode) });
  },
}));
