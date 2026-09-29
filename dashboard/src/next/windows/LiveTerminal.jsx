import { useCallback, useEffect, useRef, useState } from "react";
import { termOptions } from "../lib/prefs";
import { Eraser, X } from "lucide-react";
import { ensureXtermLoaded, wsUrl } from "../../utils/loadXterm";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";

const RETRY_S = 5;
// As for the VM console: about a minute of retries, none after a refusal (4xx), then a Retry button.
const MAX_ATTEMPTS = 12;

// A terminal (xterm) on a ticket + WebSocket relay that connects by itself and reconnects after a drop, like the VM
// console: used by the host shell and container terminal windows. getUrl() asks for a fresh ticket and returns the
// WebSocket path; the whole window is the terminal.
export default function LiveTerminal({ getUrl, label, note }) {
  const t = useT();
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const [retryIn, setRetryIn] = useState(null);
  const [gaveUp, setGaveUp] = useState(false);
  const attempts = useRef(0);
  const screen = useRef(null);
  const term = useRef(null);
  const ws = useRef(null);
  const onResize = useRef(null);

  const cleanup = useCallback(() => {
    try { ws.current?.close(); } catch { /* already closed */ }
    try { term.current?.dispose(); } catch { /* already disposed */ }
    if (onResize.current) window.removeEventListener("resize", onResize.current);
    ws.current = null; term.current = null; onResize.current = null;
    if (screen.current) screen.current.innerHTML = "";
  }, []);
  useEffect(() => cleanup, [cleanup]);

  const connect = useCallback(async () => {
    setStatus("connecting"); setError(null);
    try {
      await ensureXtermLoaded();
      const path = await getUrl();
      cleanup();
      // xterm is loaded as a UMD global; its theme is the graphite of the navigation column.
      // eslint-disable-next-line no-undef
      const tm = new Terminal(termOptions());
      // eslint-disable-next-line no-undef
      const fit = new FitAddon.FitAddon();
      tm.loadAddon(fit); tm.open(screen.current); fit.fit(); term.current = tm;
      const sock = new WebSocket(wsUrl(path));
      ws.current = sock;
      sock.onopen = () => { attempts.current = 0; setStatus("connected"); fit.fit(); sock.send("\x00" + JSON.stringify({ cols: tm.cols, rows: tm.rows })); };
      sock.onmessage = (ev) => tm.write(ev.data);
      sock.onclose = () => { tm.write(`\r\n\x1b[33m[${t("vc.closedRetry", { s: RETRY_S })}]\x1b[0m\r\n`); setStatus("idle"); };
      sock.onerror = () => setError(t("nn.shellError"));
      tm.onData((d) => { if (sock.readyState === WebSocket.OPEN) sock.send(d); });
      tm.onResize(({ cols, rows }) => { if (sock.readyState === WebSocket.OPEN) sock.send("\x00" + JSON.stringify({ cols, rows })); });
      onResize.current = () => fit.fit();
      window.addEventListener("resize", onResize.current);
      tm.focus();
    } catch (e) {
      setError(errorMessage(e)); setStatus("error");
      // Not allowed, or no such container: asking again every few seconds cannot help (and each ticket is audited).
      if (e?.status >= 400 && e.status < 500) setGaveUp(true);
    }
  }, [getUrl, cleanup, t]);

  // Right away the first time, then every RETRY_S seconds while it fails or after a drop.
  useEffect(() => {
    if (gaveUp || (status !== "idle" && status !== "error")) { setRetryIn(null); return undefined; }
    if (attempts.current >= MAX_ATTEMPTS) { setGaveUp(true); setRetryIn(null); return undefined; }
    const delay = attempts.current === 0 ? 0 : RETRY_S;
    attempts.current += 1;
    setRetryIn(delay || null);
    let left = delay;
    const tick = delay ? setInterval(() => { left -= 1; setRetryIn(left > 0 ? left : null); }, 1000) : null;
    const go = setTimeout(() => { if (tick) clearInterval(tick); setRetryIn(null); connect(); }, delay * 1000);
    return () => { clearTimeout(go); if (tick) clearInterval(tick); };
  }, [status, gaveUp]); // eslint-disable-line react-hooks/exhaustive-deps

  const connected = status === "connected";
  const retryNow = () => { attempts.current = 0; setError(null); setGaveUp(false); };
  const stateLabel = connected ? t("nn.connected") : status === "connecting" ? t("nn.connecting") : retryIn ? t("vc.retryIn", { s: retryIn }) : gaveUp ? t("vc.stopped") : t("nn.notConnected");
  return (
    <div className="nx-console-standalone">
      {note}
      <div className="nx-termbar">
        <span className="nx-st" style={{ fontSize: "var(--fs-125)" }}><span className="nx-dot" data-tone={connected ? "success" : error ? "warning" : "offline"} aria-hidden="true" /><span role="status">{stateLabel}</span></span>
        <span className="nx-sp" />
        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={!connected} onClick={() => term.current?.clear()}><Eraser size={14} aria-hidden="true" />{t("nn.clear")}</button>
        <button type="button" className="nx-btn nx-btn--sm" onClick={() => window.close()}><X size={14} aria-hidden="true" />{t("vc.closeWindow")}</button>
      </div>
      <div className="nx-termwrap nx-screen">
        <div className="nx-term nx-term--screen" ref={screen} role="region" aria-label={label} />
      </div>
      {error && <p className="nx-f-h is-error" role="alert">{error}</p>}
      {gaveUp && <div className="nx-inline"><span className="nx-f-h">{t("vc.gaveUp")}</span><button type="button" className="nx-btn nx-btn--sm" onClick={retryNow}>{t("action.retry")}</button></div>}
    </div>
  );
}
