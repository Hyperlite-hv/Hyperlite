import { useEffect, useState } from "react";

// The installation's label when it is not production (HYPERLITE_ENV_LABEL on the server, say "DEV"), read once
// from /health. Shown in the top bar and on the sign-in page, and put in front of the tab title, so a
// development instance open next to production is never mistaken for it.
let pending = null;

export function fetchEnvironmentLabel() {
  if (!pending) {
    pending = fetch("/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((h) => (typeof h?.environment === "string" && h.environment) || null)
      .catch(() => null);
  }
  return pending;
}

export function useEnvironmentLabel() {
  const [label, setLabel] = useState(null);
  useEffect(() => {
    let alive = true;
    fetchEnvironmentLabel().then((l) => { if (alive) setLabel(l); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    if (!label) return;
    const base = document.title.replace(/^\[[^\]]*\] /, "");
    document.title = `[${label}] ${base}`;
  }, [label]);
  return label;
}
