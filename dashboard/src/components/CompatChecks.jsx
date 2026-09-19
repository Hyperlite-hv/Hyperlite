import { useState } from "react";

const STYLE = {
  blocking: ["text-status-error", "Bloquant"],
  warning: ["text-status-warning", "Attention"],
  ok: ["text-status-running", "OK"],
};

// Liste de controles de compatibilite (forme {statut, message, action?} de
// app/core/cluster_compat.py) : les points non satisfaits d'abord, les OK
// repliables pour ne pas noyer l'essentiel.
export default function CompatChecks({ report }) {
  const [showOk, setShowOk] = useState(false);
  if (!report) return null;
  const bad = report.controles.filter((c) => c.statut !== "ok");
  const ok = report.controles.filter((c) => c.statut === "ok");
  const rows = showOk ? [...bad, ...ok] : bad;
  return (
    <div className="w-full space-y-1.5 text-sm">
      {bad.length === 0 && <div className="text-status-running">Tous les contrôles de compatibilité sont satisfaits.</div>}
      {rows.map((c, i) => {
        const [cls, label] = STYLE[c.statut];
        return (
          <div key={`${c.id}-${i}`} className="flex items-start justify-between gap-4">
            <div>
              <div className="text-anthracite-100">{c.message}</div>
              {c.action && <div className="text-xs text-anthracite-300">Action : {c.action}</div>}
            </div>
            <span className={`shrink-0 text-xs font-medium ${cls}`}>{label}</span>
          </div>
        );
      })}
      {ok.length > 0 && (
        <button type="button" className="text-xs text-anthracite-300 underline" onClick={() => setShowOk((v) => !v)}>
          {showOk ? "Masquer" : "Voir"} les {ok.length} contrôle(s) satisfait(s)
        </button>
      )}
    </div>
  );
}
