import { useEffect, useRef, useState } from "react";
import { fetchVMMetrics } from "../api/client";

// Polls the real GET /vms/{name}/metrics (each call takes ~0.4s because it
// samples CPU/disk/network at two instants to compute a rate). Accumulates the
// received points in a sliding window to draw a history. NOT PERSISTED: reloading
// the page starts from zero (the persisted history lives in MetricsHistoryCard).
const MAX_POINTS = 120;
const POLL_MS = 4000;

export function useLiveVMMetrics(vmName, active) {
  const [data, setData] = useState([]);
  const [current, setCurrent] = useState(null);
  const [error, setError] = useState(null);
  const timerRef = useRef(null);

  useEffect(() => {
    setData([]);
    setCurrent(null);
    setError(null);
    if (!vmName || !active) return undefined;

    let cancelled = false;
    async function poll() {
      try {
        const m = await fetchVMMetrics(vmName);
        if (cancelled) return;
        if (m.etat !== "actif") return;
        const netIn = (m.reseaux || []).reduce((a, r) => a + (r.reception_ko_s || 0), 0);
        const netOut = (m.reseaux || []).reduce((a, r) => a + (r.emission_ko_s || 0), 0);
        const point = {
          t: Date.now(),
          cpu: (m.cpu_pourcent ?? 0) / 100,
          ram: m.memoire_allouee_mo ? (m.memoire_utilisee_mo ?? 0) / m.memoire_allouee_mo : 0,
          ramUseeMo: m.memoire_utilisee_mo, ramAlloueeMo: m.memoire_allouee_mo,
          netIn, netOut, disques: m.disques,
        };
        setCurrent(point);
        setData((prev) => [...prev.slice(-(MAX_POINTS - 1)), point]);
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) timerRef.current = setTimeout(poll, POLL_MS);
      }
    }
    poll();

    return () => { cancelled = true; clearTimeout(timerRef.current); };
  }, [vmName, active]);

  return { data, current, error };
}
