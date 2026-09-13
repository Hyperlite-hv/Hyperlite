import { TerminalSquare, ExternalLink, ShieldAlert } from "lucide-react";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";

// Lanceur du shell hote (voir app/routers/host.py + HostShellWindow.jsx),
// meme principe que VMConsoleTab : s'ouvre dans une fenetre separee plutot
// que dans l'onglet courant, pour garder le tableau de bord utilisable
// pendant que la session shell reste ouverte a cote.
export default function NodeShellTab({ resource: node }) {
  const isAdmin = useAuthStore(selectIsAdmin);

  if (!isAdmin) {
    return <p className="text-sm text-anthracite-400">Shell hôte réservé au rôle admin.</p>;
  }
  if (!node) return null;

  function openWindow() {
    window.open("/host-shell", "hyperlite-host-shell", "width=1100,height=750,noopener");
  }

  return (
    <div className="card flex flex-col items-center gap-3 p-10 text-center">
      <TerminalSquare size={28} className="text-anthracite-400" />
      <p className="text-sm text-anthracite-300 max-w-md">
        Ouvre un shell root interactif directement sur <strong>{node.nom}</strong>, la machine physique.
        Chaque session est journalisée (créée/fermée) et visible dans l'onglet Tâches.
      </p>
      <div className="flex items-center gap-1.5 text-xs text-status-error">
        <ShieldAlert size={13} /> Accès équivalent root — à utiliser avec prudence.
      </div>
      <button className="btn-primary" onClick={openWindow}>
        <ExternalLink size={14} /> Ouvrir dans une nouvelle fenêtre
      </button>
    </div>
  );
}
