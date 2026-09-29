import { create } from "zustand";

// Personal display preferences, kept in this browser (like the language and theme): they change how pages look
// for this person on this machine, never what the server does, so they need no account storage.
const KEY = "hyperlite-next-prefs";
export const TERM_FONTS = {
  plex: "IBM Plex Mono, ui-monospace, monospace",
  system: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  dejavu: "DejaVu Sans Mono, monospace",
  courier: "Courier New, monospace",
};
export const TERM_SIZES = [10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24];
const DEFAULTS = { termFont: "plex", termFontSize: 13, overviewPools: null };

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      termFont: TERM_FONTS[raw.termFont] ? raw.termFont : DEFAULTS.termFont,
      termFontSize: TERM_SIZES.includes(raw.termFontSize) ? raw.termFontSize : DEFAULTS.termFontSize,
      overviewPools: Array.isArray(raw.overviewPools) ? raw.overviewPools.filter((k) => typeof k === "string") : null,
    };
  } catch { return { ...DEFAULTS }; }
}

export const usePrefs = create((set, get) => ({
  ...load(),
  update(patch) {
    set(patch);
    const { termFont, termFontSize, overviewPools } = get();
    try { localStorage.setItem(KEY, JSON.stringify({ termFont, termFontSize, overviewPools })); } catch { /* applies for this session only */ }
  },
  reset() { get().update({ ...DEFAULTS }); },
}));

// xterm.js options every terminal of the interface shares (web SSH, node shell, container output).
export function termOptions() {
  const { termFont, termFontSize } = usePrefs.getState();
  return { cursorBlink: true, fontSize: termFontSize, fontFamily: TERM_FONTS[termFont], theme: { background: "#141215", foreground: "#F1ECEE", cursor: "#CE9DB2" } };
}

// Key of a storage pool in the overview selection.
export const poolKey = (p) => `${p.node}:${p.nom}`;
