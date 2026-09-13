import { useState } from "react";
import { Monitor, TerminalSquare, ExternalLink } from "lucide-react";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";

// Ouvre la console/le terminal dans une fenetre navigateur separee (comme
// Proxmox/vSphere) plutot que dans l'onglet courant : on garde le tableau de
// bord utilisable pendant qu'une console reste ouverte a cote. La logique de
// connexion reelle (WebSocket + noVNC/xterm.js) vit dans ConsolePanel, rendue
// par la page dediee src/console/ConsoleWindow.jsx que cette fenetre ouvre.
export default function VMConsoleTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const [mode, setMode] = useState("vnc"); // "vnc" | "terminal"

  if (!isAdmin) {
    return <p className="text-sm text-anthracite-400">Console réservée au rôle admin.</p>;
  }
  if (!vm) return null;

  function openWindow() {
    const url = `/console/${encodeURIComponent(vm.nom)}?mode=${mode}`;
    // Nom de fenetre stable (par VM + mode) : cliquer plusieurs fois refocalise
    // la meme fenetre au lieu d'en ouvrir une nouvelle a chaque fois.
    window.open(url, `hyperlite-console-${vm.nom}-${mode}`, "width=1100,height=750,noopener");
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-0.5 rounded-md bg-anthracite-700 p-0.5 w-fit">
        <button
          onClick={() => setMode("vnc")}
          className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium ${mode === "vnc" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
        >
          <Monitor size={13} /> Console graphique (VNC)
        </button>
        <button
          onClick={() => setMode("terminal")}
          className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium ${mode === "terminal" ? "bg-accent-blue text-white" : "text-anthracite-300"}`}
        >
          <TerminalSquare size={13} /> Terminal SSH
        </button>
      </div>

      <div className="card flex flex-col items-center gap-3 p-10 text-center">
        {mode === "vnc" ? <Monitor size={28} className="text-anthracite-400" /> : <TerminalSquare size={28} className="text-anthracite-400" />}
        <p className="text-sm text-anthracite-300">
          {mode === "vnc" ? "La console graphique" : "Le terminal SSH"} s'ouvre dans une fenêtre séparée, pour garder
          ce tableau de bord utilisable pendant que la connexion reste ouverte.
        </p>
        <button className="btn-primary" disabled={vm.etat !== "actif"} onClick={openWindow}>
          <ExternalLink size={14} /> Ouvrir dans une nouvelle fenêtre
        </button>
        {vm.etat !== "actif" && <p className="text-xs text-anthracite-500">La VM doit être démarrée.</p>}
      </div>
    </div>
  );
}
