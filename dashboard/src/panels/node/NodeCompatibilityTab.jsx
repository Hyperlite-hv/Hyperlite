import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { fetchNodeCapabilitiesById, fetchHostPreflight, fetchNodeCompatibility } from "../../api/client";
import CompatChecks from "../../components/CompatChecks";
import { flattenCapabilities, deriveFeatures, NA } from "../../lib/capabilitiesView";

const FEATURE_STYLE = {
  actif: ["text-status-running", "Actif"],
  limite: ["text-status-warning", "Limité"],
  inconnu: ["text-anthracite-400", "Inconnu"],
};
const PREFLIGHT_STYLE = {
  ok: ["text-status-running", "OK"],
  warning: ["text-status-warning", "Attention"],
  disabled: ["text-status-warning", "Désactivé"],
  blocking: ["text-status-error", "Bloquant"],
};

function Section({ title, children }) {
  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-anthracite-600 text-sm font-semibold text-anthracite-100">{title}</div>
      <div className="divide-y divide-anthracite-600">{children}</div>
    </div>
  );
}

// Reel : GET /host/capabilities | /nodes/{name}/capabilities (chantier 1)
// et GET /host/preflight (chantier 3, hote local uniquement -- il sonde le
// venv du service, pas d'equivalent distant pour l'instant).
export default function NodeCompatibilityTab({ resource: node }) {
  const nodeId = node?.id;
  const isLocal = nodeId === "local";
  const [caps, setCaps] = useState(null);
  const [preflight, setPreflight] = useState(null);
  const [pairCompat, setPairCompat] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    if (!nodeId) return;
    setLoading(true);
    setError(null);
    const jobs = [fetchNodeCapabilitiesById(nodeId).then(setCaps)];
    if (isLocal) jobs.push(fetchHostPreflight().then(setPreflight).catch(() => setPreflight(null)));
    else {
      setPreflight(null);
      setPairCompat(null);
      jobs.push(fetchNodeCompatibility(nodeId).then(setPairCompat).catch(() => setPairCompat(null)));
    }
    Promise.all(jobs).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [nodeId, isLocal]);

  useEffect(() => { setCaps(null); load(); }, [load]);

  if (!node) return null;

  const rows = flattenCapabilities(caps);
  const sections = [...new Set(rows.map((r) => r.section))];
  const features = deriveFeatures(caps);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-anthracite-300">
          Ce que ce nœud peut réellement faire, détecté (jamais supposé). "{NA}" = non détectable depuis ici.
        </p>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Actualiser
        </button>
      </div>

      {error && <div className="card px-4 py-3 text-sm text-status-error">Erreur : {error}</div>}
      {!caps && !error && <div className="card px-4 py-3 text-sm text-anthracite-400">Détection en cours...</div>}

      {features.length > 0 && (
        <Section title="Fonctionnalités">
          {features.map((f) => {
            const [cls, label] = FEATURE_STYLE[f.etat];
            return (
              <div key={f.id} className="flex items-start justify-between gap-4 px-4 py-3 text-sm">
                <div>
                  <div className="text-anthracite-100">{f.label}</div>
                  {f.detail && <div className="mt-0.5 text-xs text-anthracite-300">{f.detail}</div>}
                </div>
                <span className={`shrink-0 font-medium ${cls}`}>{label}</span>
              </div>
            );
          })}
        </Section>
      )}

      {pairCompat && (
        <Section title="Compatibilité avec l'hôte local (source -> ce nœud)">
          <div className="px-4 py-3"><CompatChecks report={pairCompat} /></div>
        </Section>
      )}

      {preflight && (
        <Section title={`Preflight check (${preflight.resume.compte.blocking} bloquant, ${preflight.resume.compte.disabled} désactivé, ${preflight.resume.compte.warning} attention)`}>
          {preflight.controles.filter((c) => c.statut !== "ok").length === 0 && (
            <div className="px-4 py-3 text-sm text-status-running">Tous les contrôles sont satisfaits.</div>
          )}
          {preflight.controles.filter((c) => c.statut !== "ok").map((c) => {
            const [cls, label] = PREFLIGHT_STYLE[c.statut];
            return (
              <div key={c.id} className="flex items-start justify-between gap-4 px-4 py-3 text-sm">
                <div>
                  <div className="text-anthracite-100">{c.message}</div>
                  {c.fonctionnalite && <div className="mt-0.5 text-xs text-anthracite-300">Impact : {c.fonctionnalite}</div>}
                  {c.action && <div className="mt-0.5 text-xs text-anthracite-300">Action : {c.action}</div>}
                </div>
                <span className={`shrink-0 font-medium ${cls}`}>{label}</span>
              </div>
            );
          })}
        </Section>
      )}

      {sections.map((s) => (
        <Section key={s} title={s}>
          {rows.filter((r) => r.section === s).map((r) => (
            <div key={r.key} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm">
              <span className="text-anthracite-300">{r.label}</span>
              <span className={`text-right font-mono ${r.value === NA ? "text-anthracite-400" : "text-anthracite-100"}`}>{String(r.value)}</span>
            </div>
          ))}
        </Section>
      ))}
    </div>
  );
}
