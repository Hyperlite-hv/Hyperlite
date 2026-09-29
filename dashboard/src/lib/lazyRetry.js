import { lazy } from "react";

const RELOADS = "hyperlite-chunk-reloads";
const WINDOW_MS = 60_000;
const MAX_RELOADS = 3;

// Loads a code-split module; when its file cannot be fetched (a network change or drop while it downloads, or files
// replaced by an update since the page was opened), reloads the page instead of leaving it blank for good. Retrying
// in the same page does not help: the browser keeps a failed module import and never fetches it again, and React's
// lazy() keeps the rejected promise. At most three reloads a minute, recorded in sessionStorage, so a server that
// is really down ends in the error rather than a reload loop.
export async function loadOrReload(load, { reload, storage, now } = {}) {
  const store = storage || window.sessionStorage;
  const at = (now || Date.now)();
  try {
    return await load();
  } catch (error) {
    let recent = [];
    try { recent = JSON.parse(store.getItem(RELOADS) || "[]").filter((t) => at - t < WINDOW_MS); } catch { recent = []; }
    if (recent.length >= MAX_RELOADS) throw error;
    store.setItem(RELOADS, JSON.stringify([...recent, at]));
    (reload || (() => window.location.reload()))();
    return new Promise(() => {}); // the page is going away
  }
}

export function lazyRetry(load) {
  return lazy(() => loadOrReload(load));
}
