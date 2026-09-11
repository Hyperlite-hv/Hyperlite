import { CalendarClock } from "lucide-react";

// Mock uniquement : pas de sauvegardes planifiees cote backend (voir aussi
// VMBackupTab). Seuls les snapshots qcow2 existent reellement.
export default function BackupsTab() {
  return (
    <div className="card flex flex-col items-center gap-2 p-8 text-center">
      <CalendarClock size={26} className="text-anthracite-400" />
      <p className="text-sm text-anthracite-300">Aucune tache de sauvegarde planifiee.</p>
      <p className="text-xs text-anthracite-500 max-w-sm">Fonctionnalite non implementee cote backend Hyperlite pour le moment.</p>
    </div>
  );
}
