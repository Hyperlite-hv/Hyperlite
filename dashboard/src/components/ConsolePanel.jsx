import { useEffect, useRef, useState } from "react";
import { Monitor, TerminalSquare, Plug, Unplug } from "lucide-react";
import { createConsoleTicket, createTerminalTicket } from "../api/client";
import { ensureXtermLoaded, wsUrl } from "../utils/loadXterm";

// Real WebSocket relay (VNC through noVNC, terminal through xterm.js) to a VM,
// using the same ticket + WS flow as the existing backend. Extracted from
// VMConsoleTab so it can be reused as is by the dedicated console window
// (src/console/ConsoleWindow.jsx) AND by the launcher embedded in a VM's Console
// tab.
export default function ConsolePanel({ vmName, vmActive, initialMode = "vnc" }) {
  const [mode, setMode] = useState(initialMode);
  const [status, setStatus] = useState("idle"); // idle | connecting | connected | error
  const [error, setError] = useState(null);

  const screenRef = useRef(null);
  const rfbRef = useRef(null);
  const termRef = useRef(null);
  const wsRef = useRef(null);
  const resizeHandlerRef = useRef(null);

  function cleanup() {
    if (rfbRef.current) { try { rfbRef.current.disconnect(); } catch { /* ignore */ } rfbRef.current = null; }
    if (wsRef.current) { try { wsRef.current.close(); } catch { /* ignore */ } wsRef.current = null; }
    if (termRef.current) { try { termRef.current.dispose(); } catch { /* ignore */ } termRef.current = null; }
    if (resizeHandlerRef.current) { window.removeEventListener("resize", resizeHandlerRef.current); resizeHandlerRef.current = null; }
    if (screenRef.current) screenRef.current.innerHTML = "";
    setStatus("idle");
  }

  useEffect(() => cleanup, [vmName, mode]);

  async function connectVnc() {
    setStatus("connecting"); setError(null);
    try {
      const ticket = await createConsoleTicket(vmName);
      const url = wsUrl(`/vms/${encodeURIComponent(vmName)}/console?ticket=${encodeURIComponent(ticket.ticket)}`);
      // Bypasses Rollup's static analysis (which would otherwise try to resolve this
      // path as a bundle module): rfb.js lives in public/novnc/, outside the module
      // graph, and must be loaded as is at runtime.
      const rfbUrl = new URL("/novnc/core/rfb.js", window.location.origin).href;
      const mod = await import(/* @vite-ignore */ rfbUrl);
      const RFB = mod.default;
      screenRef.current.innerHTML = "";
      const rfb = new RFB(screenRef.current, url);
      rfb.scaleViewport = true; // fills the container instead of showing the VM's native resolution tiny
      rfbRef.current = rfb;
      rfb.addEventListener("connect", () => {
        setStatus("connected");
        // The remote resolution is only known once the connection is established:
        // re-assigning scaleViewport here forces noVNC to recompute the scale with the
        // real size (doing it only at construction time is not enough, the remote size
        // is still 0 at that point).
        rfb.scaleViewport = true;
      });
      rfb.addEventListener("disconnect", () => setStatus("idle"));
      rfb.addEventListener("credentialsrequired", () => {
        setError("This VM requires VNC credentials that Hyperlite does not manage.");
        setStatus("error");
      });
    } catch (e) {
      setError(e.message); setStatus("error");
    }
  }

  async function connectTerminal() {
    setStatus("connecting"); setError(null);
    try {
      await ensureXtermLoaded();
      const ticket = await createTerminalTicket(vmName);
      const url = wsUrl(`/vms/${encodeURIComponent(vmName)}/terminal?ticket=${encodeURIComponent(ticket.ticket)}`);

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
      ws.onclose = () => { term.write("\r\n\x1b[33m[connection closed]\x1b[0m\r\n"); setStatus("idle"); };
      ws.onerror = () => setError("Terminal connection error.");

      term.onData((data) => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
      term.onResize(({ cols, rows }) => { if (ws.readyState === WebSocket.OPEN) ws.send("\x00" + JSON.stringify({ cols, rows })); });

      resizeHandlerRef.current = () => fitAddon.fit();
      window.addEventListener("resize", resizeHandlerRef.current);
    } catch (e) {
      setError(e.message); setStatus("error");
    }
  }

  function connect() {
    if (mode === "vnc") connectVnc(); else connectTerminal();
  }

  // Automatic connection: no need to click "Connect" by hand, as soon as the VM is
  // active we connect on our own (on mount, on a VNC/terminal tab change, or as soon
  // as the VM starts while the console was already open). Does not trigger a
  // reconnection loop on a simple drop (status change alone, outside the
  // dependencies), only on a real change of VM/mode/active state.
  useEffect(() => {
    if (vmActive && status === "idle") connect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vmName, mode, vmActive]);

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="flex gap-0.5 rounded-md bg-anthracite-700 p-0.5">
          <button
            onClick={() => { cleanup(); setMode("vnc"); }}
            className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium ${mode === "vnc" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
          >
            <Monitor size={13} /> Graphical console (VNC)
          </button>
          <button
            onClick={() => { cleanup(); setMode("terminal"); }}
            className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium ${mode === "terminal" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
          >
            <TerminalSquare size={13} /> SSH terminal
          </button>
        </div>

        {status === "connected" ? (
          <button className="btn-secondary ml-auto" onClick={cleanup}><Unplug size={13} /> Disconnect</button>
        ) : (
          <button className="btn-primary ml-auto" disabled={!vmActive || status === "connecting"} onClick={connect}>
            <Plug size={13} /> {status === "connecting" ? "Connecting..." : "Connect"}
          </button>
        )}
      </div>

      {!vmActive && <p className="text-xs text-anthracite-500">The VM must be started.</p>}
      {error && <p className="text-xs text-status-error">{error}</p>}

      <div className="flex-1 min-h-[420px] rounded-lg overflow-hidden bg-black border border-anthracite-600">
        <div ref={screenRef} className="h-full w-full" />
      </div>
    </div>
  );
}
