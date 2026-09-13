import { CalendarClock } from "lucide-react";

// Pas de sauvegarde planifiee ni de sauvegarde independante cote backend
// Hyperlite aujourd'hui -- seuls les snapshots qcow2 existent (voir
// VMSnapshotsTab, reels), et ils ne survivent pas a la perte du disque
// source. Vue purement informative, pas d'action factice ici.
export default function VMBackupTab() {
  return (
    <div className="card flex flex-col items-center gap-2 p-8 text-center">
      <CalendarClock size={26} className="text-anthracite-400" />
      <p className="text-sm text-anthracite-300">Aucune sauvegarde planifiée.</p>
      <p className="text-xs text-anthracite-500 max-w-sm">
        Non implémenté côté backend Hyperlite. Les snapshots (onglet Snapshots) restent sur le même
        disque et ne remplacent pas une sauvegarde indépendante.
      </p>
    </div>
  );
}
