import { Save } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useTaskSimulator } from "../../hooks/useTaskSimulator";

// Mock uniquement : pas de sauvegarde planifiee cote backend Hyperlite (seuls
// les snapshots qcow2 existent, voir VMSnapshotsTab -- ceux-la sont reels).
export default function VMBackupTab({ resource: vm }) {
  const addTask = useInfraStore((s) => s.addTask);
  const { simulateTask } = useTaskSimulator();

  function runBackupNow() {
    const taskId = addTask({ type: "create_snapshot", cible: `${vm.nom} (sauvegarde complete)`, node: vm.node });
    simulateTask(taskId, { durationMs: 4000 });
  }

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <p className="text-sm text-anthracite-300">Aucune sauvegarde planifiee configuree pour cette VM.</p>
        <button className="btn-primary mt-3" onClick={runBackupNow}>
          <Save size={14} /> Sauvegarder maintenant
        </button>
      </div>
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-anthracite-100 mb-2">Planification</h3>
        <p className="text-sm text-anthracite-400">Aucune regle. (mock -- non branche sur un backend reel)</p>
      </div>
    </div>
  );
}
