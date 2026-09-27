import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { fetchNodeCapabilitiesById, fetchHostPreflight, fetchNodeCompatibility } from "../../api/client";
import CompatChecks from "../../components/CompatChecks";
import { flattenCapabilities, deriveFeatures, NA } from "../../lib/capabilitiesView";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const FEATURE_STYLE = {
  actif: ["text-status-running", "Active"],
  limite: ["text-status-warning", "Limited"],
  inconnu: ["text-muted-foreground", "Unknown"],
};
const PREFLIGHT_STYLE = {
  ok: ["text-status-running", "OK"],
  warning: ["text-status-warning", "Attention"],
  disabled: ["text-status-warning", "Disabled"],
  blocking: ["text-status-error", "Blocking"],
};

function Section({ title, children }) {
  return (
    <Card className="p-0">
      <div className="px-4 py-3 border-b border-border text-sm font-semibold text-foreground">{title}</div>
      <div className="divide-y divide-border">{children}</div>
    </Card>
  );
}

// Real: GET /host/capabilities | /nodes/{name}/capabilities and GET
// /host/preflight (local host only: it probes the service venv, there is no
// remote equivalent for now).
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
        <p className="text-sm text-foreground/80">
          What this node can really do, detected (never assumed). "{NA}" = not detectable from here.
        </p>
        <Button variant="secondary" onClick={load} disabled={loading}>
          <RefreshCw className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>

      {error && <Card className="px-4 py-3 text-sm text-status-error">Error: {error}</Card>}
      {!caps && !error && <Card className="px-4 py-3 text-sm text-muted-foreground">Detection in progress...</Card>}

      {features.length > 0 && (
        <Section title="Features">
          {features.map((f) => {
            const [cls, label] = FEATURE_STYLE[f.etat];
            return (
              <div key={f.id} className="flex items-start justify-between gap-4 px-4 py-3 text-sm transition-colors duration-150 hover:bg-muted/40">
                <div>
                  <div className="text-foreground">{f.label}</div>
                  {f.detail && <div className="mt-0.5 text-xs text-foreground/80">{f.detail}</div>}
                </div>
                <span className={`shrink-0 font-medium ${cls}`}>{label}</span>
              </div>
            );
          })}
        </Section>
      )}

      {pairCompat && (
        <Section title="Compatibility with the local host (source -> this node)">
          <div className="px-4 py-3"><CompatChecks report={pairCompat} /></div>
        </Section>
      )}

      {preflight && (
        <Section title={`Preflight check (${preflight.resume.compte.blocking} blocking, ${preflight.resume.compte.disabled} disabled, ${preflight.resume.compte.warning} warning)`}>
          {preflight.controles.filter((c) => c.statut !== "ok").length === 0 && (
            <div className="px-4 py-3 text-sm text-status-running">All checks passed.</div>
          )}
          {preflight.controles.filter((c) => c.statut !== "ok").map((c) => {
            const [cls, label] = PREFLIGHT_STYLE[c.statut];
            return (
              <div key={c.id} className="flex items-start justify-between gap-4 px-4 py-3 text-sm transition-colors duration-150 hover:bg-muted/40">
                <div>
                  <div className="text-foreground">{c.message}</div>
                  {c.fonctionnalite && <div className="mt-0.5 text-xs text-foreground/80">Impact: {c.fonctionnalite}</div>}
                  {c.action && <div className="mt-0.5 text-xs text-foreground/80">Action: {c.action}</div>}
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
            <div key={r.key} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm transition-colors duration-150 hover:bg-muted/40">
              <span className="text-foreground/80">{r.label}</span>
              <span className={`text-right font-mono ${r.value === NA ? "text-muted-foreground" : "text-foreground"}`}>{String(r.value)}</span>
            </div>
          ))}
        </Section>
      ))}
    </div>
  );
}
