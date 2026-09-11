import { TerminalSquare } from "lucide-react";

// Zone qui simule une console pour l'instant. Le vrai Hyperlite a DEJA une
// console VNC (noVNC) et un terminal SSH (xterm.js) fonctionnels, tous deux en
// relais WebSocket via FastAPI -- voir app/static/app.js (openConsole/openTerminal)
// et les endpoints /vms/{name}/console + /vms/{name}/terminal cote backend.
// Pour brancher le vrai composant ici : soit reimplementer le meme relais
// WebSocket en React (xterm.js + son addon-fit sont deja vendorises sous
// app/static/xterm/ et peuvent etre repris tels quels), soit, en solution rapide,
// embarquer la page existante dans une <iframe src="/legacy/vm/{nom}/console">.
export default function VMConsoleTab({ resource: vm }) {
  return (
    <div className="card flex flex-col items-center justify-center gap-3 p-10 text-center">
      <TerminalSquare size={32} className="text-anthracite-400" />
      <div>
        <p className="text-sm font-medium text-anthracite-100">Console de {vm?.nom}</p>
        <p className="mt-1 text-xs text-anthracite-400 max-w-sm">
          Zone de simulation -- brancher noVNC / xterm.js ici pour une vraie console interactive
          (l'implementation reelle existe deja cote Hyperlite, voir le commentaire dans ce fichier).
        </p>
      </div>
      <button className="btn-primary" disabled={vm?.etat !== "actif"}>
        Se connecter
      </button>
      {vm?.etat !== "actif" && <p className="text-xs text-anthracite-500">La VM doit etre demarree.</p>}
    </div>
  );
}
