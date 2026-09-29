import { useCallback } from "react";
import { create } from "zustand";
import en from "./en";
import fr from "./fr";

const CATALOGS = { en, fr };
const KEY = "hyperlite-next-lang";

export function detectLang() {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored && CATALOGS[stored]) return stored;
  } catch { /* storage unavailable */ }
  const nav = typeof navigator !== "undefined" ? (navigator.language || "en") : "en";
  return nav.toLowerCase().startsWith("fr") ? "fr" : "en";
}

export const useLangStore = create((set) => ({
  lang: detectLang(),
  setLang(lang) {
    if (!CATALOGS[lang]) return;
    try { localStorage.setItem(KEY, lang); } catch { /* applies for this session only */ }
    document.documentElement.lang = lang;
    set({ lang });
  },
}));

export function translate(lang, key, vars) {
  let s = CATALOGS[lang]?.[key] ?? CATALOGS.en[key] ?? key;
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
