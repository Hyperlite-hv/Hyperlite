import { useEffect, useState } from "react";
import { fetchHostLimits } from "../api/client";

// Per-VM bounds (GET /host/limits). Hyperlite sets no ceiling of its own: a bound whose source is "technique" (a
// typo guard only) is turned into max: null, so the forms show no upper bound; an administrator's cap
// (source "configuration") is kept. On failure, returns null and the backend validation decides.
let cached = null;

function withoutTechnicalMax(limits) {
  const out = { ...limits };
  for (const [key, bound] of Object.entries(limits)) {
    if (bound && bound.source === "technique") out[key] = { ...bound, max: null };
  }
  return out;
}

export function useHostLimits() {
  const [limits, setLimits] = useState(cached);
  useEffect(() => {
    let alive = true;
    fetchHostLimits()
      .then((l) => { cached = withoutTechnicalMax(l); if (alive) setLimits(cached); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return limits;
}
