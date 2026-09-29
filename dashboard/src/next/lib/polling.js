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

    let running = false;
    const schedule = (ms) => { clearTimeout(timer); if (!stopped) timer = setTimeout(tick, ms); };
    const tick = async () => {
      if (stopped) return;
      if (document.hidden) { schedule(baseMs); return; }
      // Coming back to the tab while a call is still in flight must not start a second chain of calls.
      if (running) return;
      running = true;
      try { await ref.current(); delay = baseMs; } catch { delay = Math.min(delay * 2, baseMs * 5); }
      finally { running = false; }
      schedule(delay);
    };
    const onVisible = () => { if (!document.hidden && !running) { clearTimeout(timer); tick(); } };
    document.addEventListener("visibilitychange", onVisible);
    timer = setTimeout(tick, baseMs);
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [baseMs, enabled]);
}
