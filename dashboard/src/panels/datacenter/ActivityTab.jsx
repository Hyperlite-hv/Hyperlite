import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import StatusBadge from "../../components/StatusBadge";
import { fetchTasks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";

// Meme table `tasks` persistee que NodeTasksTab.jsx (GET /tasks, voir
// app/core/tasks.py) mais sans filtre par nœud : "Activité récente" au
// niveau Datacenter, toutes machines/conteneurs confondus -- equivalent
// Hyperlite du panneau "Recent Tasks" d'un tableau de bord vSphere/Proxmox.
const TASK_LABELS = {
  create_vm: "Créer VM", delete_vm: "Supprimer VM", start_vm: "Démarrer VM",
  stop_vm: "Arrêter VM", force_stop_vm: "Arrêt forcé VM", restart_vm: "Redémarrer VM",
  clone_vm: "Cloner VM", auto_install: "Installation automatisée",
  create_snapshot: "Créer snapshot", delete_snapshot: "Supprimer snapshot", restore_snapshot: "Restaurer snapshot",
  backup_vm: "Sauvegarder VM", restore_backup: "Restaurer sauvegarde",
  export_vm: "Exporter VM", upload_vm_disk: "Téléverser disque",
  create_container: "Créer conteneur", upload_iso: "Téléverser ISO",
  host_shell: "Shell hôte", hyperlite_update: "Mise à jour Hyperlite", run_job: "Job d'automatisation",
};

const STATUT_ETAT = { en_cours: "avertissement", termine: "actif", echec: "erreur", en_attente: "avertissement" };

function formatHeure(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDuree(debut, fin) {
  if (!debut) return "--";
  const endMs = fin ? new Date(fin).getTime() : Date.now();
  const s = Math.max(0, Math.round((endMs - new Date(debut).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function ActivityTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [statutFiltre, setStatutFiltre] = useState("");

  const load = useCallback(() => {
    fetchTasks({ statut: statutFiltre || undefined, limit: 200 })
      .then(setRows)
      .catch((e) => pushToast({ kind: "error", title: "Erreur tâches", message: e.message }));
  }, [statutFiltre, pushToast]);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select
          value={statutFiltre}
          onChange={(e) => setStatutFiltre(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100"
        >
          <option value="">Tous les statuts</option>
          <option value="en_cours">En cours</option>
          <option value="termine">Terminé</option>
          <option value="echec">Échec</option>
        </select>
        <button
          onClick={load}
          className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-anthracite-300 hover:text-anthracite-100 border border-anthracite-600 rounded-md"
        >
          <RefreshCw size={14} /> Actualiser
        </button>
      </div>

      <div className="card divide-y divide-anthracite-600 max-h-[70vh] overflow-y-auto">
        <div className="grid grid-cols-[150px_1fr_1fr_120px_110px_80px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400 sticky top-0 bg-anthracite-800">
          <span>Heure</span><span>Tâche</span><span>Cible</span><span>Nœud</span><span>Utilisateur</span><span>Durée</span>
        </div>

        {rows == null && <div className="px-4 py-3 text-sm text-anthracite-400">Chargement...</div>}
        {rows && rows.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucune activité récente.</div>}

        {rows && rows.map((t) => (
          <div key={t.id} className="grid grid-cols-[150px_1fr_1fr_120px_110px_80px] gap-2 px-4 py-2 text-sm items-center">
            <span className="text-anthracite-400 text-xs font-mono">{formatHeure(t.cree_le)}</span>
            <span className="flex items-center gap-1.5 text-anthracite-100">
              <StatusBadge etat={STATUT_ETAT[t.statut]} showLabel={false} />
              {TASK_LABELS[t.type] || t.type}
            </span>
            <span className="text-anthracite-300 truncate" title={t.erreur || ""}>{t.cible || "--"}</span>
            <span className="text-anthracite-400 text-xs truncate">{t.node || "--"}</span>
            <span className="text-anthracite-400 text-xs truncate">{t.username || "--"}</span>
            <span className="text-anthracite-400 text-xs">{formatDuree(t.debut_le, t.fin_le)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
