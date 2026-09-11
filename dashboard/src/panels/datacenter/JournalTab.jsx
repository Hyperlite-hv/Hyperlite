import { useEffect, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { fetchAuditLog } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";

// Reel : GET /audit, qui lit la table audit_log alimentee depuis le debut par
// chaque endpoint du backend (log_action() est appele partout) -- cette donnee
// existait deja, elle n'etait simplement exposee par aucun des deux fronts.
export default function JournalTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [entries, setEntries] = useState(null);

  useEffect(() => {
    fetchAuditLog(200)
      .then(setEntries)
      .catch((e) => pushToast({ kind: "error", title: "Erreur journal", message: e.message }));
  }, [pushToast]);

  if (entries == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  return (
    <div className="card divide-y divide-anthracite-600 max-h-[70vh] overflow-y-auto">
      <div className="grid grid-cols-[110px_100px_1fr_1fr_70px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400 sticky top-0 bg-anthracite-800">
        <span>Heure</span><span>Utilisateur</span><span>Action</span><span>Ressource</span><span>Resultat</span>
      </div>
      {entries.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucune entree.</div>}
      {entries.map((e) => (
        <div key={e.id} className="grid grid-cols-[110px_100px_1fr_1fr_70px] gap-2 px-4 py-2 text-sm items-center">
          <span className="text-anthracite-400 text-xs font-mono">{new Date(e.timestamp).toLocaleString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit", day: "2-digit", month: "2-digit" })}</span>
          <span className="text-anthracite-200 truncate">{e.username || "--"}</span>
          <span className="text-anthracite-100 font-mono text-xs truncate">{e.action}</span>
          <span className="text-anthracite-300 truncate" title={e.error_message || ""}>{e.resource || "--"}</span>
          <span>
            {e.result === "succes"
              ? <CheckCircle2 size={14} className="text-status-running" />
              : <XCircle size={14} className="text-status-error" />}
          </span>
        </div>
      ))}
    </div>
  );
}
