import { useEffect, useRef, useState } from "react";
import { Plug, Unplug } from "lucide-react";
import { createContainerTerminalTicket } from "../api/client";
import { ensureXtermLoaded, wsUrl } from "../utils/loadXterm";

// SSH terminal of a container: the same ticket + WebSocket + xterm.js relay as
// HostShellPanel/ConsolePanel (per-VM SSH terminal), a different backend entry
// point (/containers/{name}/terminal, see app/routers/containers.py).
export default function ContainerShellPanel({ name }) {
  const [status, setStatus] = useState("idle"); // idle | connecting | connected | error
  const [error, setError] = useState(null);

  const screenRef = useRef(null);
  const termRef = useRef(null);
  const wsRef = useRef(null);
  const resizeHandlerRef = useRef(null);

  function cleanup() {
    if (wsRef.current) { try { wsRef.current.close(); } catch { /* ignore */ } wsRef.current = null; }
    if (termRef.current) { try { termRef.current.dispose(); } catch { /* ignore */ } termRef.current = null; }
    if (resizeHandlerRef.current) { window.removeEventListener("resize", resizeHandlerRef.current); resizeHandlerRef.current = null; }
    if (screenRef.current) screenRef.current.innerHTML = "";
    setStatus("idle");
  }

  useEffect(() => cleanup, [name]);

  async function connect() {
    setStatus("connecting"); setError(null);
    try {
      await ensureXtermLoaded();
      const ticket = await createContainerTerminalTicket(name);
      const url = wsUrl(`/containers/${encodeURIComponent(name)}/terminal?ticket=${encodeURIComponent(ticket.ticket)}`);

      screenRef.current.innerHTML = "";
      // eslint-disable-next-line no-undef
      const term = new Terminal({ cursorBlink: true, fontSize: 13, theme: { background: "#000000" } });
      // eslint-disable-next-line no-undef
      const fitAddon = new FitAddon.FitAddon();
      term.loadAddon(fitAddon);
      term.open(screenRef.current);
      fitAddon.fit();
      termRef.current = term;

      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => {
        setStatus("connected");
        fitAddon.fit();
        ws.send("\x00" + JSON.stringify({ cols: term.cols, rows: term.rows }));
      };
      ws.onmessage = (ev) => term.write(ev.data);
      ws.onclose = () => { term.write("\r\n\x1b[33m[session ended]\x1b[0m\r\n"); setStatus("idle"); };
      ws.onerror = () => setError("Container terminal connection error.");

      term.onData((data) => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
      term.onResize(({ cols, rows }) => { if (ws.readyState === WebSocket.OPEN) ws.send("\x00" + JSON.stringify({ cols, rows })); });

      resizeHandlerRef.current = () => fitAddon.fit();
      window.addEventListener("resize", resizeHandlerRef.current);
    } catch (e) {
      setError(e.message); setStatus("error");
    }
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-2">
        {status === "connected" ? (
          <button className="btn-secondary ml-auto" onClick={cleanup}><Unplug size={13} /> Disconnect</button>
        ) : (
          <button className="btn-primary ml-auto" disabled={status === "connecting"} onClick={connect}>
            <Plug size={13} /> {status === "connecting" ? "Connecting..." : "Open the terminal"}
          </button>
        )}
      </div>

      {error && <p className="text-xs text-status-error">{error}</p>}

      <div className="flex-1 min-h-[420px] rounded-lg overflow-hidden bg-black border border-anthracite-600">
        <div ref={screenRef} className="h-full w-full" />
      </div>
    </div>
  );
}
