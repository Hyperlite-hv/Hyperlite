import { useEffect, useRef, useState } from "react";
import { Plug, Unplug, ShieldAlert } from "lucide-react";
import { createHostTerminalTicket } from "../api/client";
import { ensureXtermLoaded, wsUrl } from "../utils/loadXterm";

// Shell interactif sur l'hote physique -- meme relais ticket + WebSocket +
// xterm.js que ConsolePanel (terminal SSH par VM), mais point d'entree
// backend different (/host/terminal, voir app/routers/host.py) : pas de SSH
// ici, un vrai pty local sur la machine qui fait tourner Hyperlite. Extrait
// dans un composant separe (plutot que reutiliser ConsolePanel) parce que ce
// mode n'a pas de VNC, pas de VM associee, et merite un avertissement de
// securite explicite en permanence a l'ecran.
export default function HostShellPanel({ hostname }) {
  const [status, setStatus] = useState("idle"); // idle | connecting | connected | error
  const [error, setError] = useState(null);

  const screenRef = useRef(null);
  const termRef = useRef(null);
  const wsRef = useRef(null);
  const resizeHandlerRef = useRef(null);

  function cleanup() {
    if (wsRef.current) { try { wsRef.current.close(); } catch (e) { /* ignore */ } wsRef.current = null; }
    if (termRef.current) { try { termRef.current.dispose(); } catch (e) { /* ignore */ } termRef.current = null; }
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
      ws.onclose = () => { term.write("\r\n\x1b[33m[session hôte terminée]\x1b[0m\r\n"); setStatus("idle"); };
      ws.onerror = () => setError("Erreur de connexion au shell hôte.");

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
        <p className="text-xs text-anthracite-200">
          Accès root complet à <strong>{hostname}</strong>, la machine physique qui héberge toutes les VM.
          Chaque ouverture/fermeture de session est journalisée (onglet Tâches + Journal) avec l'utilisateur responsable.
        </p>
      </div>

      <div className="flex items-center gap-2">
        {status === "connected" ? (
          <button className="btn-secondary ml-auto" onClick={cleanup}><Unplug size={13} /> Déconnecter</button>
        ) : (
          <button className="btn-primary ml-auto" disabled={status === "connecting"} onClick={connect}>
            <Plug size={13} /> {status === "connecting" ? "Connexion..." : "Ouvrir le shell"}
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
