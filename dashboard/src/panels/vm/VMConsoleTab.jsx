import { useState } from "react";
import { Monitor, TerminalSquare, ExternalLink } from "lucide-react";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";

// Opens the console/terminal in a separate browser window (like Proxmox/vSphere)
// rather than in the current tab: the dashboard stays usable while a console
// remains open next to it. The real connection logic (WebSocket + noVNC/xterm.js)
// lives in ConsolePanel, rendered by the dedicated page src/console/ConsoleWindow.jsx
// that this window opens.
export default function VMConsoleTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const [mode, setMode] = useState("vnc"); // "vnc" | "terminal"

  if (!isAdmin) {
    return <p className="text-sm text-anthracite-400">Console reserved for the admin role.</p>;
  }
  if (!vm) return null;

  function openWindow() {
    const url = `/console/${encodeURIComponent(vm.nom)}?mode=${mode}`;
    // Stable window name (per VM + mode): clicking several times refocuses the same
    // window instead of opening a new one each time.
    window.open(url, `hyperlite-console-${vm.nom}-${mode}`, "width=1100,height=750,noopener");
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-0.5 rounded-md bg-anthracite-700 p-0.5 w-fit">
        <button
          onClick={() => setMode("vnc")}
          className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium ${mode === "vnc" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
        >
          <Monitor size={13} /> Graphical console (VNC)
        </button>
        <button
          onClick={() => setMode("terminal")}
          className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium ${mode === "terminal" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
        >
          <TerminalSquare size={13} /> SSH terminal
        </button>
      </div>

      <div className="card flex flex-col items-center gap-3 p-10 text-center">
        {mode === "vnc" ? <Monitor size={28} className="text-anthracite-400" /> : <TerminalSquare size={28} className="text-anthracite-400" />}
        <p className="text-sm text-anthracite-300">
          {mode === "vnc" ? "The graphical console" : "The SSH terminal"} opens in a separate window, to keep this dashboard usable while the connection stays open.
        </p>
        <button className="btn-primary" disabled={vm.etat !== "actif"} onClick={openWindow}>
          <ExternalLink size={14} /> Open in a new window
        </button>
        {vm.etat !== "actif" && <p className="text-xs text-anthracite-400">The VM must be started.</p>}
      </div>
    </div>
  );
}
