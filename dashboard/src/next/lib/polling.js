import { useEffect, useRef } from "react";

// Polling that pauses while the tab is hidden, backs off after failures and never overlaps
// two in-flight calls. `fn` may throw; it is retried with a growing delay (x2, max 5 x base).
export function usePolling(fn, baseMs, { enabled = true } = {}) {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; }, [fn]);

  useEffect(() => {
    if (!enabled) return undefined;
    let timer = null;
    let stopped = false;
    let delay = baseMs;

    const tick = async () => {
      if (stopped) return;
      if (document.hidden) { timer = setTimeout(tick, baseMs); return; }
      try { await ref.current(); delay = baseMs; } catch { delay = Math.min(delay * 2, baseMs * 5); }
      if (!stopped) timer = setTimeout(tick, delay);
    };
    const onVisible = () => { if (!document.hidden) { clearTimeout(timer); tick(); } };
    document.addEventListener("visibilitychange", onVisible);
    timer = setTimeout(tick, baseMs);
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [baseMs, enabled]);
}
