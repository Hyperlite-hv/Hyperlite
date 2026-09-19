import { useEffect, useMemo, useState } from "react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchNodeCapabilitiesById } from "../../api/client";
import { compareNodes, NA } from "../../lib/capabilitiesView";

// Tableau comparatif des nœuds (mandat portabilite, chantier 4) : met en
// evidence ce qui DIFFERE entre machines -- prealable a une migration ou a
// l'ajout d'un nœud. Les lignes "informatives" (RAM, modele CPU...) sont
// toujours differentes d'une machine a l'autre : signalees a part.
export default function CompatibilityTab() {
  const nodes = useInfraStore((s) => s.nodes);
  const [profiles, setProfiles] = useState({});
  const [errors, setErrors] = useState({});
  const [onlyDiff, setOnlyDiff] = useState(true);

  useEffect(() => {
    setProfiles({});
    setErrors({});
    nodes.forEach((n) => {
      fetchNodeCapabilitiesById(n.id)
        .then((p) => setProfiles((prev) => ({ ...prev, [n.id]: p })))
        .catch((e) => setErrors((prev) => ({ ...prev, [n.id]: e.message })));
    });
  }, [nodes]);

  const loaded = useMemo(() => nodes.filter((n) => profiles[n.id]), [nodes, profiles]);
  const rows = useMemo(() => compareNodes(Object.fromEntries(loaded.map((n) => [n.id, profiles[n.id]]))), [loaded, profiles]);
  const shown = onlyDiff && loaded.length > 1 ? rows.filter((r) => r.differe) : rows;
  const blocking = rows.filter((r) => r.differe && !r.informatif);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-anthracite-300">
          Comparaison des capacités détectées sur chaque nœud du cluster.
          {loaded.length === 1 && " Un seul nœud : ajoutez-en un (onglet Nœuds) pour voir les écarts."}
        </p>
        <label className="flex items-center gap-2 text-sm text-anthracite-300">
          <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} />
          Différences uniquement
        </label>
      </div>

      {Object.entries(errors).map(([id, msg]) => (
        <div key={id} className="card px-4 py-3 text-sm text-status-error">Nœud {id} : {msg}</div>
      ))}

      {loaded.length > 1 && (
        <div className={`card px-4 py-3 text-sm ${blocking.length ? "text-status-warning" : "text-status-running"}`}>
          {blocking.length
            ? `${blocking.length} écart(s) de capacités entre nœuds pouvant affecter migration ou HA : ${blocking.map((r) => r.label).join(", ")}.`
            : "Aucun écart de capacités fonctionnelles entre les nœuds."}
        </div>
      )}

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-anthracite-600 text-left text-anthracite-300">
              <th className="px-4 py-3 font-medium">Capacité</th>
              {loaded.map((n) => <th key={n.id} className="px-4 py-3 font-medium">{n.nom}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-anthracite-600">
            {shown.length === 0 && (
              <tr><td className="px-4 py-3 text-anthracite-400" colSpan={loaded.length + 1}>
                {loaded.length === 0 ? "Détection en cours..." : "Aucune différence."}
              </td></tr>
            )}
            {shown.map((r) => (
              <tr key={r.key} className={r.differe && !r.informatif ? "bg-status-warning/10" : ""}>
                <td className="px-4 py-2.5 text-anthracite-300">
                  {r.section} : {r.label}{r.differe && r.informatif ? " (informatif)" : ""}
                </td>
                {loaded.map((n) => (
                  <td key={n.id} className={`px-4 py-2.5 font-mono ${r.values[n.id] === NA ? "text-anthracite-400" : "text-anthracite-100"}`}>
                    {String(r.values[n.id])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
