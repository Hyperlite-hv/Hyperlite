import { useEffect, useRef, useState } from "react";
import { Plug, Unplug, ShieldAlert } from "lucide-react";
import { createHostTerminalTicket } from "../api/client";
import { ensureXtermLoaded, wsUrl } from "../utils/loadXterm";
import { Button } from "@/components/ui/button";

// Interactive shell on the physical host: the same ticket + WebSocket + xterm.js
// relay as ConsolePanel (per-VM SSH terminal), but a different backend entry
// point (/host/terminal, see app/routers/host.py): no SSH here, a real local pty
// on the machine running Hyperlite. Extracted into a separate component (rather
// than reusing ConsolePanel) because this mode has no VNC, no associated VM, and
// deserves an explicit security warning on screen at all times.
export default function HostShellPanel({ hostname }) {
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

  useEffect(() => cleanup, []);

  async function connect() {
    setStatus("connecting"); setError(null);
    try {
      await ensureXtermLoaded();
      const ticket = await createHostTerminalTicket();
      const url = wsUrl(`/host/terminal?ticket=${encodeURIComponent(ticket.ticket)}`);

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
      ws.onclose = () => { term.write("\r\n\x1b[33m[host session ended]\x1b[0m\r\n"); setStatus("idle"); };
      ws.onerror = () => setError("Host shell connection error.");

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
      <div className="flex items-start gap-2 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2">
        <ShieldAlert size={16} className="text-status-error shrink-0 mt-0.5" />
        <p className="text-xs text-foreground/90">
          Full root access to <strong>{hostname}</strong>, the physical machine hosting all the VMs. Every session open/close is logged (Tasks + Journal tabs) with the responsible user.
        </p>
      </div>

      <div className="flex items-center gap-2">
        {status === "connected" ? (
          <Button variant="secondary" className="ml-auto" onClick={cleanup}><Unplug /> Disconnect</Button>
        ) : (
          <Button className="ml-auto" disabled={status === "connecting"} onClick={connect}>
            <Plug /> {status === "connecting" ? "Connecting..." : "Open the shell"}
          </Button>
        )}
      </div>

      {error && <p className="text-xs text-status-error">{error}</p>}

      <div className="flex-1 min-h-[420px] rounded-lg overflow-hidden bg-black border border-border">
        <div ref={screenRef} className="h-full w-full" />
      </div>
    </div>
  );
}
