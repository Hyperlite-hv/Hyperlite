import { useEffect, useState } from "react";

// The installation's label when it is not production (HYPERLITE_ENV_LABEL on the server, say "DEV"), read once
// from /health. Shown in the top bar and on the sign-in page, and put in front of the tab title, so a
// development instance open next to production is never mistaken for it.
let pending = null;
let known = null; // the label once read
let pageTitle = "Hyperlite";

// The title of the browser tab: the page, then the product, with the installation's label in front.
export function setDocumentTitle(text) {
  pageTitle = text ? `${text} – Hyperlite` : "Hyperlite";
  document.title = known ? `[${known}] ${pageTitle}` : pageTitle;
}

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
    known = label;
    document.title = `[${label}] ${pageTitle}`;
  }, [label]);
  return label;
}
