// Loads xterm.js + addon-fit (vendored under public/xterm) on demand, as UMD
// globals (window.Terminal, window.FitAddon.FitAddon).
let loaded = false;
let loading = null;

export function ensureXtermLoaded() {
  if (loaded) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/xterm/xterm.css";
    document.head.appendChild(link);

    const s1 = document.createElement("script");
    s1.src = "/xterm/xterm.js";
    s1.onload = () => {
      const s2 = document.createElement("script");
      s2.src = "/xterm/addon-fit.js";
      s2.onload = () => { loaded = true; resolve(); };
      s2.onerror = () => reject(new Error("Unable to load the terminal resize addon."));
      document.head.appendChild(s2);
    };
    s1.onerror = () => reject(new Error("Unable to load xterm.js."));
    document.head.appendChild(s1);
  });
  return loading;
}

export function wsUrl(path) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}
