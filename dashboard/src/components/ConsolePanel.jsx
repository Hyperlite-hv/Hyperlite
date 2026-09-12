import { useEffect, useRef, useState } from "react";
import { Monitor, TerminalSquare, Plug, Unplug } from "lucide-react";
import { createConsoleTicket, createTerminalTicket } from "../api/client";
import { ensureXtermLoaded, wsUrl } from "../utils/loadXterm";

// Relais WebSocket reel (VNC via noVNC, terminal via xterm.js) vers une VM --
// meme flux ticket + WS que le backend existant. Extrait de VMConsoleTab pour
// etre reutilise tel quel par la fenetre de console dediee (src/console/ConsoleWindow.jsx)
// ET par le lanceur integre a l'onglet Console d'une VM.
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
    if (rfbRef.current) { try { rfbRef.current.disconnect(); } catch (e) { /* ignore */ } rfbRef.current = null; }
    if (wsRef.current) { try { wsRef.current.close(); } catch (e) { /* ignore */ } wsRef.current = null; }
    if (termRef.current) { try { termRef.current.dispose(); } catch (e) { /* ignore */ } termRef.current = null; }
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
      // Contourne l'analyse statique de Rollup (qui tenterait sinon de resoudre
      // ce chemin comme un module du bundle) : rfb.js vit dans public/novnc/,
      // hors du graphe de modules, et doit etre charge tel quel au runtime.
      const rfbUrl = new URL("/novnc/core/rfb.js", window.location.origin).href;
      const mod = await import(/* @vite-ignore */ rfbUrl);
      const RFB = mod.default;
      screenRef.current.innerHTML = "";
      const rfb = new RFB(screenRef.current, url);
      rfb.scaleViewport = true; // remplit le conteneur au lieu d'afficher la resolution native de la VM en tout petit
      rfbRef.current = rfb;
      rfb.addEventListener("connect", () => {
        setStatus("connected");
        // La resolution distante n'est connue qu'a la connexion etablie : re-assigner
        // scaleViewport ici force noVNC a recalculer l'echelle avec la vraie taille
        // (le faire seulement au moment de la construction ne suffit pas, la taille
        // distante vaut encore 0 a cet instant-la).
        rfb.scaleViewport = true;
      });
      rfb.addEventListener("disconnect", () => setStatus("idle"));
      rfb.addEventListener("credentialsrequired", () => {
        setError("Cette VM demande des identifiants VNC non geres par Hyperlite.");
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
      ws.onclose = () => { term.write("\r\n\x1b[33m[connexion terminee]\x1b[0m\r\n"); setStatus("idle"); };
      ws.onerror = () => setError("Erreur de connexion au terminal.");

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

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="flex gap-0.5 rounded-md bg-anthracite-700 p-0.5">
          <button
            onClick={() => { cleanup(); setMode("vnc"); }}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium ${mode === "vnc" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
          >
            <Monitor size={13} /> Console graphique (VNC)
          </button>
          <button
            onClick={() => { cleanup(); setMode("terminal"); }}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium ${mode === "terminal" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
          >
            <TerminalSquare size={13} /> Terminal SSH
          </button>
        </div>

        {status === "connected" ? (
          <button className="btn-secondary ml-auto" onClick={cleanup}><Unplug size={13} /> Deconnecter</button>
        ) : (
          <button className="btn-primary ml-auto" disabled={!vmActive || status === "connecting"} onClick={connect}>
            <Plug size={13} /> {status === "connecting" ? "Connexion..." : "Se connecter"}
          </button>
        )}
      </div>

      {!vmActive && <p className="text-xs text-anthracite-500">La VM doit etre demarree.</p>}
      {error && <p className="text-xs text-status-error">{error}</p>}

      <div className="flex-1 min-h-[420px] rounded-lg overflow-hidden bg-black border border-anthracite-600">
        <div ref={screenRef} className="h-full w-full" />
      </div>
    </div>
  );
}
