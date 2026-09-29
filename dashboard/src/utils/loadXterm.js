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

// Loads the terminal font before xterm measures its character cell: measured with the fallback font, the cell is
// the wrong size and the rows and columns sent to the shell do not match what is drawn.
export async function loadTerminalFont(options) {
  const family = String(options.fontFamily || "").split(",")[0].trim();
  if (!family || !document.fonts?.load) return;
  try {
    await document.fonts.load(`${options.fontSize || 13}px ${family}`);
  } catch {
    // Not a web font (a system one, or the browser refuses the query): xterm measures what the browser draws.
  }
}

// Fits the terminal to its box now and whenever the box changes size: window resize, zoom, display scaling
// change, sidebar folded, panel resized. Returns the function that stops following.
export function keepFitted(element, fit) {
  const refit = () => {
    if (!element.isConnected) return;
    try { fit.fit(); } catch (e) { console.warn("terminal fit failed", e); }
  };
  refit();
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(refit);
  observer?.observe(element);
  window.addEventListener("resize", refit);
  return () => { observer?.disconnect(); window.removeEventListener("resize", refit); };
}
