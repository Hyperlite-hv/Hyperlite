import { useCallback, useEffect, useRef, useState } from "react";

// Data a page reads from the server, shown at once from the last read when the page is opened again, then read again
// in the background ("stale while revalidate"): going back to a page no longer flashes a loading state. Kept for this
// page's life in the browser only.
const cache = new Map(); // key -> { data, at }

export const cachedValue = (key) => cache.get(key)?.data ?? null;
export function remember(key, data) { cache.set(key, { data, at: Date.now() }); return data; }

export function useCached(key, fetcher) {
  const [state, setState] = useState(() => ({ data: cachedValue(key), error: null }));
  const fetchRef = useRef(fetcher);
  useEffect(() => { fetchRef.current = fetcher; });
  const reload = useCallback(async () => {
    try {
      const data = await fetchRef.current();
      remember(key, data);
      setState({ data, error: null });
      return data;
    } catch (error) {
      setState((s) => ({ data: s.data, error }));
      return null;
    }
  }, [key]);
  useEffect(() => {
    setState({ data: cachedValue(key), error: null });
    reload();
  }, [key, reload]);
  // A change made here shows before the next read: the page passes the data it expects.
  const mutate = useCallback((update) => setState((s) => {
    const data = typeof update === "function" ? update(s.data) : update;
    remember(key, data);
    return { ...s, data };
  }), [key]);
  return { data: state.data, error: state.error, reload, mutate };
}

export function clearCached() { cache.clear(); }
