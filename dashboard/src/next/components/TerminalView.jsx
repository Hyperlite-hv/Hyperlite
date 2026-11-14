import { useCallback, useEffect, useRef, useState } from "react";
import { termOptions } from "../lib/prefs";
import { ensureXtermLoaded, keepFitted, loadTerminalFont, wsUrl } from "../../utils/loadXterm";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";

const RETRY_S = 3;
const MAX_ATTEMPTS = 10;

// One terminal of the docked panel: xterm on a ticket + WebSocket, re-attached by itself after a drop (a fresh ticket
// each time, as the other consoles do). It stays alive while its tab is hidden, and refits when shown. `onEnded`: the
// server said the session is gone (4xx), so the tab can say why.
export default function TerminalView({ getUrl, visible, label, onEnded }) {
  const t = useT();
  const screen = useRef(null);
  const term = useRef(null);
  const ws = useRef(null);
  const fitRef = useRef(null);
  const stopFit = useRef(null);
  const attempts = useRef(0);
  const [status, setStatus] = useState("connecting");
  const [error, setError] = useState(null);

  const connect = useCallback(async () => {
    setStatus("connecting");
    try {
      await ensureXtermLoaded();
      const path = await getUrl();
      if (!term.current) {
        const options = termOptions();
        await loadTerminalFont(options);
        // xterm is loaded as a UMD global.
        // eslint-disable-next-line no-undef
        const tm = new Terminal(options);
        // eslint-disable-next-line no-undef
        const fit = new FitAddon.FitAddon();
        tm.loadAddon(fit); tm.open(screen.current); fit.fit();
        term.current = tm; fitRef.current = fit;
        stopFit.current = keepFitted(screen.current, fit);
        tm.onData((d) => { if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(d); });
        tm.onResize(({ cols, rows }) => { if (ws.current?.readyState === WebSocket.OPEN) ws.current.send("\x00" + JSON.stringify({ cols, rows })); });
      }
      const tm = term.current;
      const sock = new WebSocket(wsUrl(path));
      ws.current = sock;
      sock.onopen = () => { attempts.current = 0; setStatus("connected"); setError(null); fitRef.current?.fit(); sock.send("\x00" + JSON.stringify({ cols: tm.cols, rows: tm.rows })); };
      sock.onmessage = (ev) => tm.write(ev.data);
      sock.onclose = () => { if (ws.current === sock) setStatus("dropped"); };
    } catch (e) {
      setError(errorMessage(e));
      if (e?.status >= 400 && e.status < 500) { setStatus("ended"); onEnded?.(errorMessage(e)); } else setStatus("dropped");
    }
  }, [getUrl, onEnded]);

  useEffect(() => { connect(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { try { ws.current?.close(); } catch { /* already closed */ } stopFit.current?.(); term.current?.dispose(); }, []);
  // A dropped connection: try again a few times (the session lives on the server meanwhile).
  useEffect(() => {
    if (status !== "dropped") return undefined;
    if (attempts.current >= MAX_ATTEMPTS) { setStatus("ended"); return undefined; }
    attempts.current += 1;
    const go = setTimeout(connect, RETRY_S * 1000);
    return () => clearTimeout(go);
  }, [status, connect]);
  useEffect(() => { if (visible) { requestAnimationFrame(() => { fitRef.current?.fit(); term.current?.focus(); }); } }, [visible]);

  return (
    <div className="hc-tv" hidden={!visible}>
      <div className="hc-tv-screen" ref={screen} role="region" aria-label={label} />
      {status !== "connected" && (
        <div className="hc-tv-state" role="status">
          {status === "connecting" ? t("nn.connecting") : status === "dropped" ? t("vc.retryIn", { s: RETRY_S }) : t("tm.ended")}
          {error && <span> · {error}</span>}
          {status === "ended" && <button type="button" className="nx-btn nx-btn--sm" onClick={() => { attempts.current = 0; connect(); }}>{t("action.retry")}</button>}
        </div>
      )}
    </div>
  );
}
