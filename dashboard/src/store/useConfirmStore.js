import { create } from "zustand";

// Promise-based confirmation: `await confirmAction({ title, message, confirmLabel })`
// resolves to true (confirmed) or false (cancelled). It is rendered by <ConfirmHost />, so
// every destructive action uses the same accessible dialog instead of window.confirm.
export const useConfirmStore = create((set) => ({
  request: null,
  ask(options) {
    return new Promise((resolve) => set({ request: { ...options, resolve } }));
  },
  answer(value) {
    set((s) => {
      s.request?.resolve(value);
      return { request: null };
    });
  },
}));

export function confirmAction(options) {
  return useConfirmStore.getState().ask(options);
}
