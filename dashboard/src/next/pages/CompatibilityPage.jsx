import { useEffect, useMemo, useState } from "react";
import { Check, X } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchNodeCapabilitiesById } from "../../api/client";
import { compareNodes, NA } from "../../lib/capabilitiesView";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { PageHeader, Card, Empty } from "../components/ui";
import { capRow } from "../lib/capsI18n";

const yes = (v) => v === true || ["oui", "yes", "present", "available", "importable"].includes(String(v).toLowerCase());
const no = (v) => v === false || ["non", "no", "absent", "unavailable"].includes(String(v).toLowerCase());

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
  const rows = useMemo(() => compareNodes(Object.fromEntries(loaded.map((n) => [n.id, profiles[n.id]]))).map((r) => ({ ...capRow(t, { ...r, value: null }), values: r.values })), [loaded, profiles, t]);
  const shown = onlyDiff && loaded.length > 1 ? rows.filter((r) => r.differe) : rows;
  const blocking = rows.filter((r) => r.differe && !r.informatif);
  const nameOf = (id) => nodes.find((n) => n.id === id)?.nom || id;
  const cell = (v) => (v === NA ? <span className="nx-muted">{t("ns.notReported")}</span>
    : yes(v) ? <span className="nx-st nx-tone-success"><Check size={14} aria-hidden="true" />{t("cp.yes")}</span>
    : no(v) ? <span className="nx-st" data-tone="offline"><X size={14} aria-hidden="true" />{t("cp.no")}</span>
    : <span className="nx-mono">{String(v)}</span>);

  return (
    <>
      <PageHeader title={t("tab.compat")} desc={t("cp.desc")} />
      <div className="nx-cols2">
        <Card title={t("cp.title")} flush note={loaded.length > 1 ? t("cp.diffNote") : null}
          actions={loaded.length > 1 && <label className="nx-check"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> {t("cp.onlyDiff")}</label>}>
          {Object.entries(errors).map(([id, msg]) => <p key={id} className="nx-error-inline" role="alert" style={{ margin: "0 var(--space-4) var(--space-2)" }}>{t("cp.nodeError", { node: nameOf(id) })} <span className="nx-mono">{msg}</span></p>)}
          {loaded.length > 1 && (
            <div className="nx-bn" data-tone={blocking.length ? "warning" : "success"} role="status" style={{ margin: "0 var(--space-4) var(--space-3)" }}>
              <span className="nx-bn-t">{blocking.length ? t("cp.blocking", { n: blocking.length, list: blocking.map((r) => r.label).join(", ") }) : t("cp.same")}</span>
            </div>
          )}
          {loaded.length === 1 && <p className="nx-muted" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("cp.oneNode")}</p>}
          {loaded.length === 0 && Object.keys(errors).length === 0 ? <p className="nx-muted" role="status" style={{ padding: "0 var(--space-4) var(--space-4)" }}>{t("cp.detecting")}</p> : shown.length === 0 ? (
            <Empty title={t("cp.noDiff")} />
          ) : (
            <div className="nx-tablewrap" role="region" aria-label={t("cp.title")} tabIndex={0}>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("cp.capability")}</th>{loaded.map((n) => <th scope="col" key={n.id}>{n.nom}</th>)}</tr></thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={r.key} className={r.differe && !r.informatif ? "is-diff" : ""}>
                      <th scope="row" style={{ fontWeight: 500 }}>{r.section} : {r.label}{r.differe && (r.informatif ? ` (${t("cp.info")})` : ` — ${t("cp.differs")}`)}</th>
                      {loaded.map((n) => <td key={n.id}>{cell(r.values[n.id])}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
CompatibilityPage.ownHeader = true;
