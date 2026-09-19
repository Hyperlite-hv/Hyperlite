import { useEffect, useState } from "react";
import { fetchHostLimits } from "../api/client";

// Resource limits derived from the host (GET /host/limits). On failure (e.g.
// unreachable host), returns null: the fields stay without an upper bound on the
// UI side and the precise backend validation decides. This is controlled
// degradation rather than a blocked form.
let cached = null;

export function useHostLimits() {
  const [limits, setLimits] = useState(cached);
  useEffect(() => {
    let alive = true;
    fetchHostLimits()
      .then((l) => { cached = l; if (alive) setLimits(l); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return limits;
}
