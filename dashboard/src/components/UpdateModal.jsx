import { useEffect, useState } from "react";
import { X, RefreshCw, ShieldAlert, CheckCircle2, XCircle } from "lucide-react";
import ProgressBar from "./ProgressBar";
import { fetchUpdateCheck, applyUpdate, fetchTaskDetail } from "../api/client";

const STEP_ORDER = [
  [5, "Sauvegarde de l'état actuel"],
  [20, "Récupération de la dernière version"],
  [30, "Application de la nouvelle version"],
  [45, "Dépendances Python"],
  [55, "Dépendances front"],
  [70, "Reconstruction de l'interface"],
  [85, "Vérification du schéma"],
  [90, "Redémarrage du service"],
];

// Reel : GET /update/check + POST /update/apply (voir app/routers/update.py,
// chantier 7). Le redemarrage final est verifie cote NAVIGATEUR (poll de
// /health) -- le backend qui pilote la mise a jour meurt avec le restart, il
// ne peut pas verifier son propre remplacement (voir scripts/update_watchdog.sh
// pour le vrai filet de securite cote serveur).
export default function UpdateModal({ onClose }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | updating | restarting | ok | failed
  const [task, setTask] = useState(null);

  useEffect(() => {
    fetchUpdateCheck().then(setInfo).catch((e) => setError(e.message));
  }, []);

  async function handleApply() {
    setPhase("updating");
    setError(null);
    try {
      const { task_id } = await applyUpdate();
      for (;;) {
        const t = await fetchTaskDetail(task_id);
        setTask(t);
        if (t.statut === "echec") { setPhase("failed"); setError(t.erreur); return; }
        if (t.statut === "termine") break;
        await new Promise((r) => setTimeout(r, 1000));
      }
      setPhase("restarting");
      // Le service redemarre ~2s apres la fin de la tache : on attend une
      // reponse de /health, avec un delai genereux (build front + redemarrage).
      const deadline = Date.now() + 60000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const res = await fetch("/health");
          if (res.ok) { setPhase("ok"); return; }
        } catch (e) { /* service en cours de redemarrage, normal */ }
      }
      setPhase("failed");
      setError("Le service ne répond plus après 60s. Le watchdog serveur a peut-être dû restaurer l'ancienne version — vérifiez manuellement.");
    } catch (e) {
      setPhase("failed");
      setError(e.message);
    }
  }

  const currentStepIdx = task ? STEP_ORDER.findIndex(([pct]) => pct >= (task.progres ?? 0)) : -1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-[520px] max-w-[90vw] p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-anthracite-100">Mise à jour d'Hyperlite</h3>
          <button onClick={onClose} className="text-anthracite-400 hover:text-anthracite-100"><X size={18} /></button>
        </div>

        {error && <p className="text-sm text-status-error">{error}</p>}

        {phase === "idle" && info && (
          <>
            {!info.verifiable && <p className="text-sm text-anthracite-300">{info.erreur}</p>}
            {info.verifiable && (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-anthracite-400">Version locale</span><span className="font-mono text-anthracite-200">{info.commit_local?.slice(0, 8)}</span></div>
                <div className="flex justify-between"><span className="text-anthracite-400">Version distante</span><span className="font-mono text-anthracite-200">{info.commit_distant?.slice(0, 8) ?? "--"}</span></div>
                <div className="flex justify-between"><span className="text-anthracite-400">Statut</span><span className={info.a_jour ? "text-status-running" : "text-status-warning"}>{info.a_jour ? "À jour" : "Nouvelle version disponible"}</span></div>

                {info.changelog?.length > 0 && (
                  <div className="mt-2 rounded-md bg-anthracite-700/60 p-2 max-h-32 overflow-y-auto font-mono text-xs text-anthracite-300">
                    {info.changelog.map((l, i) => <div key={i}>{l}</div>)}
                  </div>
                )}

                {!info.arbre_propre && (
                  <div className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 mt-2">
                    <ShieldAlert size={14} className="text-status-warning shrink-0 mt-0.5" />
                    <p className="text-xs text-anthracite-200">
                      Arbre de travail non propre (modifications non commitées) — la mise à jour est bloquée pour éviter un conflit. Commitez ou annulez les changements locaux d'abord.
                    </p>
                  </div>
                )}

                <p className="text-xs text-anthracite-500 mt-2">
                  Ne touche que l'API et l'interface Hyperlite — les VM déjà actives ne sont ni arrêtées ni redémarrées.
                  Une sauvegarde complète est prise avant toute modification, avec restauration automatique en cas d'échec.
                </p>
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button className="btn-secondary" onClick={onClose}>Fermer</button>
              <button
                className="btn-primary"
                disabled={!info.verifiable || info.a_jour || !info.arbre_propre}
                onClick={handleApply}
              >
                <RefreshCw size={14} /> Mettre à jour
              </button>
            </div>
          </>
        )}

        {(phase === "updating" || phase === "restarting") && (
          <div className="space-y-3">
            <ProgressBar value={task?.progres ?? 5} statut="en_cours" />
            <div className="text-sm text-anthracite-200">
              {phase === "restarting" ? "Redémarrage du service, vérification en cours..." : (STEP_ORDER[currentStepIdx]?.[1] ?? "Préparation...")}
            </div>
            <p className="text-xs text-anthracite-500">Ne fermez pas cette fenêtre.</p>
          </div>
        )}

        {phase === "ok" && (
          <div className="flex flex-col items-center gap-2 py-4">
            <CheckCircle2 size={32} className="text-status-running" />
            <p className="text-sm text-anthracite-100">Mise à jour appliquée, le service répond.</p>
            <button className="btn-primary mt-2" onClick={() => window.location.reload()}>Recharger la page</button>
          </div>
        )}

        {phase === "failed" && (
          <div className="flex flex-col items-center gap-2 py-4">
            <XCircle size={32} className="text-status-error" />
            <p className="text-sm text-anthracite-100 text-center">Échec de la mise à jour.</p>
            <button className="btn-secondary mt-2" onClick={onClose}>Fermer</button>
          </div>
        )}
      </div>
    </div>
  );
}
