import { create } from "zustand";

// The Terminal's docked panel (docs/design/workbench.md, section 11): open or not, its height, its tabs. Kept in this
// browser so the panel comes back after a reload, re-attached to the same sessions (the server keeps them).
const KEY = "hyperlite.terminalDock";
const MIN_H = 160;

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "null");
    if (raw && Array.isArray(raw.tabs)) return raw;
  } catch { /* private window, or something unreadable: start closed */ }
  return { open: false, height: 320, maximized: false, tabs: [], active: null };
}

function save(state) {
  try {
    const { open, height, maximized, tabs, active } = state;
    localStorage.setItem(KEY, JSON.stringify({ open, height, maximized, tabs, active }));
  } catch { /* the panel still works for this page */ }
}

let counter = 0;
const newId = () => `t${Date.now().toString(36)}${(counter++).toString(36)}`;

export const useTerminalDock = create((set, get) => {
  const change = (patch) => { set(patch); save(get()); };
  return {
    ...load(),
    toggle() { change({ open: !get().open }); },
    show() { change({ open: true }); },
    hide() { change({ open: false }); },
    setHeight(h) { change({ height: Math.max(MIN_H, Math.round(h)), maximized: false }); },
    toggleMax() { change({ maximized: !get().maximized }); },
    // kind: "workbench" (sid set once the session exists), "root" (a node's root shell), "ssh" (a VM's terminal).
    add(tab) {
      const id = newId();
      change({ tabs: [...get().tabs, { id, ...tab }], active: id, open: true });
      return id;
    },
    update(id, patch) { change({ tabs: get().tabs.map((t) => (t.id === id ? { ...t, ...patch } : t)) }); },
    remove(id) {
      const tabs = get().tabs.filter((t) => t.id !== id);
      const active = get().active === id ? tabs[tabs.length - 1]?.id || null : get().active;
      change({ tabs, active, open: tabs.length ? get().open : false });
    },
    activate(id) { change({ active: id, open: true }); },
  };
});
