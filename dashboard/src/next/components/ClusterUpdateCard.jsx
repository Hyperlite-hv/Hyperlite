import { useEffect, useState } from "react";
import { RefreshCw, Rocket } from "lucide-react";
import { fetchCluster, fetchClusterUpdatePlan, startClusterUpdate } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { InlineError } from "./States";
import { Card, TableWrap } from "./ui";

// Rolling update of the cluster (app/core/rolling_update.py): the plan first, read from every node, then the run,
// followed in Activity › Tasks. Shown next to the cluster card, in a cluster only.
export default function ClusterUpdateCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [inCluster, setInCluster] = useState(false);
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [stopBlocked, setStopBlocked] = useState(false);
  const [started, setStarted] = useState(null);

  useEffect(() => { fetchCluster().then((s) => setInCluster(Boolean(s?.in_cluster))).catch(() => {}); }, []);
  if (!inCluster) return null;

  async function prepare() {
    setLoading(true);
    setError(null);
    try { setPlan(await fetchClusterUpdatePlan()); }
    catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  }

  async function start() {
    const order = plan.nodes.map((n) => n.name).join(" → ");
    if (!(await confirmAction({ title: t("cu.confirmTitle"), message: t("cu.confirmMsg", { order }), confirmLabel: t("cu.start") }))) return;
    try {
      const r = await startClusterUpdate(stopBlocked);
      setStarted(r.task_id);
      pushToast({ kind: "success", title: t("cu.started"), message: t("cu.startedMsg") });
    } catch (e) {
      pushToast({ kind: "error", title: t("cu.failed"), message: errorMessage(e) });
    }
  }

  const blocked = plan?.nodes.some((n) => n.vms_blocked?.length && n.restart);
  return (
    <Card title={t("cu.title")} actions={<button type="button" className="nx-btn" disabled={loading} onClick={prepare}><RefreshCw size={15} aria-hidden="true" />{t(plan ? "cu.refresh" : "cu.prepare")}</button>}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)" }}>{t("cu.intro")}</p>
      {loading && <p className="nx-muted" role="status">{t("cu.reading")}</p>}
      {error && <InlineError message={error} onRetry={prepare} />}
      {plan && !loading && (
        <>
          {plan.problems.length > 0 && (
            <div className="nx-bn" data-tone="danger" role="alert">
              <strong>{t("cu.blockedTitle")}</strong>
              <ul style={{ margin: "var(--space-1) 0 0", paddingLeft: "1.2em" }}>{plan.problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
          )}
          <TableWrap label={t("cu.title")}>
            <table className="nx-table">
              <thead><tr>
                <th scope="col">{t("cu.node")}</th><th scope="col">Hyperlite</th><th scope="col">{t("cu.debian")}</th>
                <th scope="col">{t("cu.reboot")}</th><th scope="col">{t("cu.vmsMoved")}</th><th scope="col">{t("cu.vmsStaying")}</th>
              </tr></thead>
              <tbody>
                {plan.nodes.map((n) => (
                  <tr key={n.name}>
                    <th scope="row">{n.name}{n.local && <span className="nx-f-h"> — {t("cu.thisNodeLast")}</span>}</th>
                    <td>{!n.reachable || !n.verifiable ? <span className="nx-error">{n.error || t("cu.unreachable")}</span>
                      : n.hyperlite_up_to_date ? t("cu.current", { version: n.version }) : `${n.version} → ${n.candidate}`}</td>
                    <td>{n.packages ?? "—"}</td>
                    <td>{n.restart ? t("cu.yes") : t("cu.no")}</td>
                    <td>{n.vms_to_migrate?.length ?? 0}</td>
                    <td>{n.vms_blocked?.length ? n.vms_blocked.join(", ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          {blocked && (
            <label className="nx-check" style={{ marginTop: "var(--space-3)" }}>
              <input type="checkbox" checked={stopBlocked} onChange={(e) => setStopBlocked(e.target.checked)} /> {t("cu.stopBlocked")}
            </label>
          )}
          <p className="nx-f-h" style={{ margin: "var(--space-3) 0" }}>{t("cu.how")}</p>
          {started
            ? <p role="status">{t("cu.startedMsg")}</p>
            : <button type="button" className="nx-btn nx-btn--primary" disabled={plan.problems.length > 0 || !plan.to_do} onClick={start}><Rocket size={15} aria-hidden="true" />{plan.to_do ? t("cu.start") : t("cu.allCurrent")}</button>}
        </>
      )}
    </Card>
  );
}
