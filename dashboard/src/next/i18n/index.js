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

export function useT() {
  const lang = useLangStore((s) => s.lang);
  return (key, vars) => translate(lang, key, vars);
}

export const LANGS = [{ id: "en", label: "English" }, { id: "fr", label: "Français" }];
export { CATALOGS };
