import { create } from "zustand";

// Promise-based text prompt (replaces window.prompt): `await promptText({ title, label, defaultValue })`
// resolves to the entered text, or null when cancelled. Rendered by <ConfirmHost />.
export const usePromptStore = create((set) => ({
  request: null,
  ask(options) { return new Promise((resolve) => set({ request: { ...options, resolve } })); },
  answer(value) { set((s) => { s.request?.resolve(value); return { request: null }; }); },
}));

export function promptText(options) { return usePromptStore.getState().ask(options); }
