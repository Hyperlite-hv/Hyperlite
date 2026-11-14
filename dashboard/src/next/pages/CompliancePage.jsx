import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Wrench } from "lucide-react";
import { fetchCompliance, fixCompliance } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { canArea, useAccess } from "../lib/access";
import { ErrorState } from "../components/States";
import { TagChips } from "../components/NotesCard";
import { Card, Chip, Empty, Loading, PageHeader, TableWrap } from "../components/ui";

// The compliance review (docs/design/governance.md, section 1): the VMs already there against the guardrails and the
// pools' envelopes, with what can be fixed from here (a missing required tag).
export default function CompliancePage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const canFix = canArea(useAccess((s) => s.me), "cluster.manage");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [all, setAll] = useState(false);
  const load = useCallback(() => fetchCompliance().then((d) => { setData(d); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  async function fix(names) {
    try {
      const { added } = await fixCompliance(names);
      const n = Object.values(added).filter((x) => x.length).length;
      pushToast({ kind: "success", title: t("cpl.fixed", { n }) });
      load();
    } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;
  const s = data.summary;
  const shown = data.vms.filter((v) => all || v.issues.length);
  const fixable = data.vms.filter((v) => v.issues.some((i) => i.fixable)).map((v) => v.name);
  const scope = (x) => (x === "cluster" ? t("acc.wholeCluster") : `${t("acc.kind.pool")} ${x.slice(5)}`);
  return (
    <>
      <PageHeader title={t("tab.compliance")} desc={t("cpl.desc")} actions={canFix && fixable.length > 0 && <button type="button" className="nx-btn nx-btn--primary" onClick={() => fix(fixable)}><Wrench size={14} aria-hidden="true" /> {t("cpl.fixAll", { n: fixable.length })}</button>} />
      <div className="hc-cp-sum">
        <div className="hc-cp-pct" data-level={s.percent >= 90 ? "ok" : s.percent >= 60 ? "mid" : "low"}><strong>{s.percent} %</strong><span>{t("cpl.compliant", { n: s.compliant, total: s.vms })}</span></div>
        <p className="nx-muted">{data.rules ? t("cpl.rules", { n: data.rules, node: data.node }) : t("cpl.noRules")}</p>
      </div>
      <Card title={t("cpl.vms")} flush actions={<label className="nx-check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> {t("cpl.showAll")}</label>}>
        {shown.length === 0 ? <Empty icon={CheckCircle2} title={t("cpl.allGood")} text={t("cpl.allGoodText")} /> : (
          <TableWrap label={t("cpl.vms")}>
            <table className="nx-table">
              <thead><tr><th scope="col">VM</th><th scope="col">{t("pl.members")}</th><th scope="col">{t("pl.tags")}</th><th scope="col">{t("cpl.issues")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {shown.map((v) => (
                  <tr key={v.name}>
                    <th scope="row"><button type="button" className="nx-lnk nx-mono" onClick={() => navigateTo("vm", v.name, "summary")}>{v.name}</button></th>
                    <td>{v.pools.join(", ") || <span className="nx-muted">—</span>}</td>
                    <td><TagChips tags={v.tags} /></td>
                    <td>{v.issues.length === 0 ? <Chip tone="success">{t("cpl.ok")}</Chip> : v.issues.map((i, k) => <div key={k}><Chip tone={i.fixable ? "warning" : "danger"}>{t(`cpl.rule.${i.rule}`)}</Chip> <small className="nx-muted">{i.detail} · {scope(i.scope)}</small></div>)}</td>
                    <td><div className="nx-ra">{canFix && v.issues.some((i) => i.fixable) && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => fix([v.name])}>{t("cpl.fix")}</button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
    </>
  );
}
CompliancePage.ownHeader = true;
