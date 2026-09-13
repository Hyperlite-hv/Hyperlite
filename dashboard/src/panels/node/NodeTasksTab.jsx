import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronUp, ChevronsUpDown, RefreshCw } from "lucide-react";
import StatusBadge from "../../components/StatusBadge";
import { fetchTasks, fetchTaskDetail } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";

const TASK_LABELS = {
  start_vm: "Démarrer VM", stop_vm: "Arrêter VM", force_stop_vm: "Arrêt forcé VM",
  restart_vm: "Redémarrer VM", delete_vm: "Supprimer VM", create_vm: "Créer VM",
  update_vm: "Modifier ressources", create_snapshot: "Créer snapshot",
  upload_iso: "Téléverser ISO",
};

const STATUT_ETAT = { en_cours: "avertissement", termine: "actif", echec: "erreur", en_attente: "avertissement" };
const STATUT_LABEL = { en_cours: "En cours", termine: "Terminé", echec: "Échec", en_attente: "En attente" };

const COLUMNS = [
  { key: "cree_le", label: "Heure" },
  { key: "cible", label: "Cible" },
  { key: "username", label: "Utilisateur" },
  { key: "type", label: "Tâche" },
  { key: "statut", label: "Statut" },
];

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

// Vue "Recent Tasks" façon vCenter : source de verite = table `tasks`
// persistee cote backend (GET /tasks), pas les taches ephemeres du store
// (celles-ci ne survivent qu'a la session en cours -- voir TaskLogPanel pour
// le ticker temps reel). Ici on veut l'historique reel, partage entre
// utilisateurs/onglets, avec heure de creation/debut/fin distinctes.
export default function NodeTasksTab({ resource: node }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [tri, setTri] = useState("cree_le");
  const [ordre, setOrdre] = useState("desc");
  const [statutFiltre, setStatutFiltre] = useState("");
  const [recherche, setRecherche] = useState("");
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState(null);

  const load = useCallback(() => {
    if (!node) return;
    fetchTasks({ node: node.id, statut: statutFiltre || undefined, cible: recherche || undefined, tri, ordre, limit: 300 })
      .then(setRows)
      .catch((e) => pushToast({ kind: "error", title: "Erreur tâches", message: e.message }));
  }, [node, statutFiltre, recherche, tri, ordre, pushToast]);

  useEffect(() => {
    load();
    // Rafraichissement leger pour suivre les taches en cours sans que
    // l'utilisateur ait a recharger la page -- meme esprit que le polling
    // silencieux de useInfraStore.refreshAll().
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!openId) { setDetail(null); return; }
    fetchTaskDetail(openId)
      .then(setDetail)
      .catch((e) => pushToast({ kind: "error", title: "Erreur détail tâche", message: e.message }));
  }, [openId, pushToast]);

  const toggleTri = (key) => {
    if (tri === key) { setOrdre((o) => (o === "asc" ? "desc" : "asc")); return; }
    setTri(key); setOrdre("desc");
  };

  if (!node) return null;

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
        <input
          type="text"
          placeholder="Filtrer par cible..."
          value={recherche}
          onChange={(e) => setRecherche(e.target.value)}
          className="bg-anthracite-700 border border-anthracite-600 rounded-md px-2 py-1.5 text-sm text-anthracite-100 flex-1 min-w-[160px]"
        />
        <button
          onClick={load}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-anthracite-300 hover:text-anthracite-100 border border-anthracite-600 rounded-md"
        >
          <RefreshCw size={14} /> Actualiser
        </button>
      </div>

      <div className="card divide-y divide-anthracite-600 max-h-[65vh] overflow-y-auto">
        <div className="grid grid-cols-[150px_1fr_120px_160px_110px_80px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400 sticky top-0 bg-anthracite-800">
          {COLUMNS.map((c) => (
            <button key={c.key} onClick={() => toggleTri(c.key)} className="flex items-center gap-1 text-left hover:text-anthracite-100">
              {c.label}
              {tri === c.key ? (ordre === "asc" ? <ChevronUp size={12} /> : <ChevronDown size={12} />) : <ChevronsUpDown size={12} className="opacity-40" />}
            </button>
          ))}
          <span>Durée</span>
        </div>

        {rows == null && <div className="px-4 py-3 text-sm text-anthracite-400">Chargement...</div>}
        {rows && rows.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucune tâche sur ce nœud.</div>}

        {rows && rows.map((t) => (
          <div key={t.id}>
            <button
              onClick={() => setOpenId((id) => (id === t.id ? null : t.id))}
              className="w-full grid grid-cols-[150px_1fr_120px_160px_110px_80px] gap-2 px-4 py-2 text-sm items-center text-left hover:bg-anthracite-700/60"
            >
              <span className="text-anthracite-400 text-xs font-mono">{formatHeure(t.cree_le)}</span>
              <span className="text-anthracite-100 truncate">{t.cible || "--"}</span>
              <span className="text-anthracite-300 truncate">{t.username || "--"}</span>
              <span className="text-anthracite-200">{TASK_LABELS[t.type] || t.type}</span>
              <StatusBadge etat={STATUT_ETAT[t.statut]} showLabel={false} />
              <span className="text-anthracite-400 text-xs">{formatDuree(t.debut_le, t.fin_le)}</span>
            </button>

            {openId === t.id && (
              <div className="px-8 pb-3 text-xs text-anthracite-400 space-y-1 bg-anthracite-800/60">
                <div>Statut : <span className="text-anthracite-200">{STATUT_LABEL[t.statut] || t.statut}</span></div>
                <div>Créée le : {formatHeure(t.cree_le)}</div>
                <div>Débutée le : {formatHeure(t.debut_le)}</div>
                <div>Terminée le : {formatHeure(t.fin_le)}</div>
                <div>Durée totale : {formatDuree(t.debut_le, t.fin_le)}</div>
                {t.erreur && <div className="text-status-error">Cause de l'échec : {t.erreur}</div>}
                {detail && detail.id === t.id && detail.logs?.length > 0 && (
                  <div className="pt-1">
                    <div className="text-anthracite-500 mb-0.5">Journal lié à cette cible :</div>
                    {detail.logs.slice(0, 5).map((l, i) => (
                      <div key={i} className="font-mono">
                        {formatHeure(l.timestamp)} — {l.action} : {l.result}{l.error_message ? ` (${l.error_message})` : ""}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
