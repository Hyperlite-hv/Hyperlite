import { useEffect, useMemo, useState } from "react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchNodeCapabilitiesById } from "../../api/client";
import { compareNodes, NA } from "../../lib/capabilitiesView";
import DeploymentProfileCard from "../../components/DeploymentProfileCard";

// Node comparison table: highlights what DIFFERS between machines, a prerequisite
// to a migration or to adding a node. The "informational" rows (RAM, CPU model...)
// always differ from one machine to another and are reported separately.
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
      <DeploymentProfileCard />
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-anthracite-300">
          Comparison of the capabilities detected on each node of the cluster.
          {loaded.length === 1 && " Only one node: add another (Nodes tab) to see the differences."}
        </p>
        <label className="flex items-center gap-2 text-sm text-anthracite-300">
          <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} />
          Differences only
        </label>
      </div>

      {Object.entries(errors).map(([id, msg]) => (
        <div key={id} className="card px-4 py-3 text-sm text-status-error">Node {id} : {msg}</div>
      ))}

      {loaded.length > 1 && (
        <div className={`card px-4 py-3 text-sm ${blocking.length ? "text-status-warning" : "text-status-running"}`}>
          {blocking.length
            ? `${blocking.length} capability difference(s) between nodes that may affect migration or HA: ${blocking.map((r) => r.label).join(", ")}.`
            : "No functional capability differences between the nodes."}
        </div>
      )}

      <div className="card overflow-x-auto" tabIndex={0} role="region" aria-label="Node comparison">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-anthracite-600 text-left text-anthracite-300">
              <th className="px-4 py-3 font-medium">Capability</th>
              {loaded.map((n) => <th key={n.id} className="px-4 py-3 font-medium">{n.nom}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-anthracite-600">
            {shown.length === 0 && (
              <tr><td className="px-4 py-3 text-anthracite-400" colSpan={loaded.length + 1}>
                {loaded.length === 0 ? "Detection in progress..." : "No differences."}
              </td></tr>
            )}
            {shown.map((r) => (
              <tr key={r.key} className={r.differe && !r.informatif ? "bg-status-warning/10" : ""}>
                <td className="px-4 py-2.5 text-anthracite-300">
                  {r.section} : {r.label}{r.differe && r.informatif ? " (informational)" : ""}
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
