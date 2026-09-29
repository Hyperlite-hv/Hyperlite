import { create } from "zustand";
import { fetchMetaList } from "../../api/client";

// Tags of every VM, container and node, for the lists and the tag filter. Loaded once, then reloaded after an edit;
// a failure leaves the lists without tags rather than breaking them.
const keyOf = (kind, node, name) => `${kind}|${kind === "node" ? "" : node || "local"}|${name}`;

export const useMetaStore = create((set, get) => ({
  byKey: {},
  loaded: false,
  loading: false,
  async load(force = false) {
    if (get().loading || (get().loaded && !force)) return;
    set({ loading: true });
    try {
      const list = await fetchMetaList();
      const byKey = {};
      for (const m of Array.isArray(list) ? list : []) byKey[keyOf(m.kind, m.node, m.nom)] = m;
      set({ byKey, loaded: true, loading: false });
    } catch {
      set({ loading: false, loaded: true });
    }
  },
}));

export function metaOf(byKey, kind, node, name) {
  return byKey[keyOf(kind, node, name)] || null;
}

// Every tag in use, sorted, for the filter.
export function allTags(byKey, kind = null) {
  const tags = new Set();
  for (const m of Object.values(byKey)) if (!kind || m.kind === kind) m.tags.forEach((tg) => tags.add(tg));
  return [...tags].sort();
}

// What a user typed in the tag field: words separated by commas or spaces, as the server will store them.
export function parseTags(text) {
  return [...new Set(String(text || "").split(/[\s,]+/).map((x) => x.trim().toLowerCase()).filter(Boolean))];
}
export const TAG_RE = /^[a-z0-9][a-z0-9_.-]{0,31}$/;
