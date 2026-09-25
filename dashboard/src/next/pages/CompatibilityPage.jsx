import { useEffect, useMemo, useState } from "react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchNodeCapabilitiesById } from "../../api/client";
import { compareNodes, NA } from "../../lib/capabilitiesView";
import DeploymentProfileCard from "../../components/DeploymentProfileCard";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { EmptyState } from "../components/States";

// Node comparison: what differs between machines, a prerequisite to a migration or to adding a node.
// Informational rows (RAM, CPU model…) always differ and are reported separately from blocking ones.
export default function CompatibilityPage() {
  const t = useT();
  const nodes = useInfraStore((s) => s.nodes);
  const [profiles, setProfiles] = useState({});
  const [errors, setErrors] = useState({});
  const [onlyDiff, setOnlyDiff] = useState(true);

  // Keyed on ids: the store replaces the array on every refresh and that must not reset the table.
  const nodeKey = nodes.map((n) => n.id).join("|");
  useEffect(() => {
    setProfiles({}); setErrors({});
    nodeKey.split("|").filter(Boolean).forEach((id) => {
      fetchNodeCapabilitiesById(id).then((p) => setProfiles((prev) => ({ ...prev, [id]: p }))).catch((e) => setErrors((prev) => ({ ...prev, [id]: errorMessage(e) })));
    });
  }, [nodeKey]);

  const loaded = useMemo(() => nodes.filter((n) => profiles[n.id]), [nodes, profiles]);
  const rows = useMemo(() => compareNodes(Object.fromEntries(loaded.map((n) => [n.id, profiles[n.id]]))), [loaded, profiles]);
  const shown = onlyDiff && loaded.length > 1 ? rows.filter((r) => r.differe) : rows;
  const blocking = rows.filter((r) => r.differe && !r.informatif);
  const nameOf = (id) => nodes.find((n) => n.id === id)?.nom || id;

  return (
    <div className="nx-ns">
      <DeploymentProfileCard />
      <section className="nx-card" aria-labelledby="cp-title">
        <div className="nx-cardhead">
          <h2 id="cp-title">{t("cp.title")}</h2>
          <label className="nx-check"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> {t("cp.onlyDiff")}</label>
        </div>
        <p className="nx-muted" style={{ marginTop: 0 }}>{t("cp.intro")}{loaded.length === 1 && ` ${t("cp.oneNode")}`}</p>

        {Object.entries(errors).map(([id, msg]) => <p key={id} className="nx-error-inline" role="alert">{t("cp.nodeError", { node: nameOf(id) })} <span className="nx-mono">{msg}</span></p>)}

        {loaded.length > 1 && (
          <p role="status" className={blocking.length ? "nx-notice nx-notice--warning" : "nx-notice nx-notice--success"}>
            {blocking.length ? t("cp.blocking", { n: blocking.length, list: blocking.map((r) => r.label).join(", ") }) : t("cp.same")}
          </p>
        )}

        {loaded.length === 0 && Object.keys(errors).length === 0 ? <p className="nx-muted" role="status">{t("cp.detecting")}</p> : shown.length === 0 ? (
          <EmptyState title={t("cp.noDiff")} />
        ) : (
          <div className="nx-tablewrap" role="region" aria-label={t("cp.title")} tabIndex={0}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("cp.capability")}</th>{loaded.map((n) => <th scope="col" key={n.id}>{n.nom}</th>)}</tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.key} className={r.differe && !r.informatif ? "nx-row--warn" : ""}>
                    <th scope="row">{r.section} : {r.label}{r.differe && (r.informatif ? ` (${t("cp.info")})` : ` — ${t("cp.differs")}`)}</th>
                    {loaded.map((n) => <td key={n.id} className={`nx-mono ${r.values[n.id] === NA ? "nx-muted" : ""}`}>{r.values[n.id] === NA ? t("ns.notReported") : String(r.values[n.id])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
