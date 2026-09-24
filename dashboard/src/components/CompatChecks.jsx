import { useState } from "react";

const STYLE = {
  blocking: ["text-status-error", "Blocking"],
  warning: ["text-status-warning", "Attention"],
  ok: ["text-status-running", "OK"],
};

// List of compatibility checks (shape {statut, message, action?} from
// app/core/cluster_compat.py): unsatisfied items first, the OK ones collapsible so
// they do not drown out the essentials.
export default function CompatChecks({ report }) {
  const [showOk, setShowOk] = useState(false);
  if (!report) return null;
  const bad = report.controles.filter((c) => c.statut !== "ok");
  const ok = report.controles.filter((c) => c.statut === "ok");
  const rows = showOk ? [...bad, ...ok] : bad;
  return (
    <div className="w-full space-y-1.5 text-sm">
      {bad.length === 0 && <div className="text-status-running">All compatibility checks passed.</div>}
      {rows.map((c, i) => {
        const [cls, label] = STYLE[c.statut];
        return (
          <div key={`${c.id}-${i}`} className="flex items-start justify-between gap-4">
            <div>
              <div className="text-foreground">{c.message}</div>
              {c.action && <div className="text-xs text-foreground/80">Action: {c.action}</div>}
            </div>
            <span className={`shrink-0 text-xs font-medium ${cls}`}>{label}</span>
          </div>
        );
      })}
      {ok.length > 0 && (
        <button type="button" className="text-xs text-foreground/80 underline transition-colors duration-150 hover:text-foreground" onClick={() => setShowOk((v) => !v)}>
          {showOk ? "Hide" : "Show"} the {ok.length} passed check(s)
        </button>
      )}
    </div>
  );
}
