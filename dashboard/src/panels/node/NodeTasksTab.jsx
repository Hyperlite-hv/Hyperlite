import { useInfraStore } from "../../store/useInfraStore";
import ProgressBar from "../../components/ProgressBar";
import StatusBadge from "../../components/StatusBadge";

// Le store tient deja une liste de taches (a terme, remplacer fetchTasks() dans
// api/client.js par un vrai appel -- il faudra exposer l'audit_log SQLite existant
// via une nouvelle route cote backend, il n'y en a pas aujourd'hui).
export default function NodeTasksTab({ resource: node }) {
  const tasks = useInfraStore((s) => s.tasks);
  if (!node) return null;
  const nodeTasks = tasks.filter((t) => t.node === node.id);

  return (
    <div className="card divide-y divide-anthracite-600">
      {nodeTasks.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucune tache sur ce noeud.</div>}
      {nodeTasks.map((t) => (
        <div key={t.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
          <StatusBadge etat={t.statut === "termine" ? "actif" : t.statut === "echec" ? "erreur" : "avertissement"} showLabel={false} />
          <span className="text-anthracite-100 w-40">{t.type}</span>
          <span className="text-anthracite-300 flex-1">{t.cible}</span>
          {t.statut === "en_cours" && <span className="w-24"><ProgressBar value={t.progres} size="sm" /></span>}
          <span className="text-anthracite-400 text-xs">{t.utilisateur}</span>
        </div>
      ))}
    </div>
  );
}
