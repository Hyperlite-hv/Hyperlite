import { useCallback } from "react";
import { useInfraStore } from "../store/useInfraStore";

// Fait progresser une tache deja creee (store.addTask) par petits paliers, pour
// donner une vraie barre de progression animee (pas juste un spinner) aux actions
// qui, cote vrai backend Hyperlite, repondent en une seule requete synchrone
// (start/stop/restart/delete VM...). Le jour ou le backend expose de vraies
// taches asynchrones avec pourcentage (ex. migration, gros televersement), il
// suffira de remplacer les appels a simulateTask() par un polling / WebSocket
// sur l'etat reel de la tache -- le store (updateTaskProgress/completeTask)
// n'a pas besoin de changer.
export function useTaskSimulator() {
  const updateTaskProgress = useInfraStore((s) => s.updateTaskProgress);
  const completeTask = useInfraStore((s) => s.completeTask);

  const simulateTask = useCallback((taskId, { durationMs = 2200, failChance = 0.06 } = {}) => {
    const steps = 18;
    const stepMs = durationMs / steps;
    let i = 0;
    const willFail = Math.random() < failChance;
    const failAt = willFail ? Math.floor(steps * (0.4 + Math.random() * 0.4)) : -1;

    const id = setInterval(() => {
      i += 1;
      if (i === failAt) {
        clearInterval(id);
        completeTask(taskId, "echec", "Ressource indisponible sur le noeud cible");
        return;
      }
      const progres = Math.min(100, Math.round((i / steps) * 100));
      updateTaskProgress(taskId, progres);
      if (i >= steps) {
        clearInterval(id);
        completeTask(taskId, "termine");
      }
    }, stepMs);

    return () => clearInterval(id);
  }, [updateTaskProgress, completeTask]);

  return { simulateTask };
}
