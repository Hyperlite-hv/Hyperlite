import { useCallback } from "react";
import { create } from "zustand";
// Each language is a file of its own, loaded when it is used: both made a 320 kB script that every sign-in downloaded
// whole (#354). The language shown first is loaded before the dashboard renders (main.jsx).
const LOADERS = { en: () => import("./en"), fr: () => import("./fr") };
const CATALOGS = {};
const KEY = "hyperlite-next-lang";

export function loadLang(lang) {
  if (CATALOGS[lang]) return Promise.resolve(CATALOGS[lang]);
  return LOADERS[lang]().then((m) => { CATALOGS[lang] = m.default; return m.default; });
}

export function detectLang() {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored && LOADERS[stored]) return stored;
  } catch { /* storage unavailable */ }
  const nav = typeof navigator !== "undefined" ? (navigator.language || "en") : "en";
  return nav.toLowerCase().startsWith("fr") ? "fr" : "en";
}

export const useLangStore = create((set) => ({
  lang: detectLang(),
  setLang(lang) {
    if (!LOADERS[lang]) return;
    try { localStorage.setItem(KEY, lang); } catch { /* applies for this session only */ }
    // The switch happens once the new language is there: no screen of raw keys meanwhile.
    loadLang(lang).then(() => { document.documentElement.lang = lang; set({ lang }); }).catch(() => {});
  },
}));

export function translate(lang, key, vars) {
  let s = CATALOGS[lang]?.[key] ?? CATALOGS.en?.[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

// The same function for as long as the language does not change: pages list `t` among the dependencies of the
// effects that load their data, and a new function at each render made those effects run again at each render
// (the network list asked the server hundreds of times a second).
export function useT() {
  const lang = useLangStore((s) => s.lang);
  return useCallback((key, vars) => translate(lang, key, vars), [lang]);
}

export const LANGS = [{ id: "en", label: "English" }, { id: "fr", label: "Français" }];
export { CATALOGS };
