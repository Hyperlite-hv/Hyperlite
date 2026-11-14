import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Wallet } from "lucide-react";
import { deleteBudget, fetchCosts, fetchPools, saveBudget, saveCostPrices } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { canArea, useAccess } from "../lib/access";
import { ErrorState } from "../components/States";
import { TagChips } from "../components/NotesCard";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

// Costs and budgets (docs/design/governance.md, section 10): the month's estimate of each VM at the prices set (vCPU
// and memory while it runs, disk always), per pool and tag, and budgets that alert at a threshold.
export default function CostsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const me = useAccess((s) => s.me);
  const admin = !!me?.admin;
  const manage = canArea(me, "cluster.manage");
  const [data, setData] = useState(null);
  const [pools, setPools] = useState([]);
  const [error, setError] = useState(null);
  const [prices, setPrices] = useState(null);
  const [budget, setBudget] = useState(null);
  const load = useCallback(async () => {
    try { const d = await fetchCosts(); setData(d); setPrices(d.prices); setPools(await fetchPools().catch(() => [])); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  async function act(fn, title) {
    try { await fn(); pushToast({ kind: "success", title }); load(); return true; } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); return false; }
  }
  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;
  const cur = data.prices.currency;
  const money = (n) => `${n.toFixed(2)} ${cur}`;
  const scopeLabel = (s) => (s === "cluster" ? t("acc.wholeCluster") : s.startsWith("pool:") ? `${t("acc.kind.pool")} ${pools.find((p) => String(p.id) === s.slice(5))?.name || s.slice(5)}` : `${t("pl.tags")} ${s.slice(4)}`);
  const unpriced = !data.prices.vcpu && !data.prices.memory_gb && !data.prices.disk_gb;
  return (
    <>
      <PageHeader title={t("tab.costs")} desc={t("co.desc", { node: data.node })} />
      <div className="hc-cp-sum">
        <div className="hc-cp-pct"><strong>{money(data.total)}</strong><span>{t("co.month")}</span></div>
        {unpriced && <p className="nx-muted">{t("co.noPrices")}</p>}
      </div>
      <Card title={t("co.budgets")} note={t("co.budgetsNote")} flush actions={manage && <button type="button" className="nx-btn nx-btn--primary" onClick={() => setBudget({ name: "", scope: "cluster", monthly: 100, alert_percent: 80 })}><Plus size={14} aria-hidden="true" /> {t("co.newBudget")}</button>}>
        {data.budgets.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("co.noBudget")}</p> : (
          <TableWrap label={t("co.budgets")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("pl.name")}</th><th scope="col">{t("sec.on")}</th><th scope="col">{t("co.spent")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.budgets.map((b) => (
                  <tr key={b.id}>
                    <th scope="row">{b.name}</th>
                    <td>{scopeLabel(b.scope)}</td>
                    <td><span className="hc-usage" title={`${b.percent} %`}><span className="nx-mono">{money(b.spent)} / {money(b.monthly)}</span><span className="hc-usage-bar" aria-hidden="true"><span style={{ width: `${Math.min(100, b.percent)}%` }} data-level={b.percent >= 100 ? "high" : b.percent >= b.alert_percent ? "mid" : "low"} /></span></span></td>
                    <td><div className="nx-ra">{manage && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("sec.remove")} onClick={() => act(() => deleteBudget(b.id), t("co.removed"))}><Trash2 size={14} aria-hidden="true" /></button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <div className="nx-cols2">
        <Card title={t("co.byPool")}>{Object.keys(data.by_pool).length === 0 ? <p className="nx-muted">—</p> : Object.entries(data.by_pool).sort((a, b) => b[1] - a[1]).map(([k, v]) => <p key={k} className="hc-co-row"><span>{k}</span><strong className="nx-mono">{money(v)}</strong></p>)}</Card>
        <Card title={t("co.byTag")}>{Object.keys(data.by_tag).length === 0 ? <p className="nx-muted">—</p> : Object.entries(data.by_tag).sort((a, b) => b[1] - a[1]).map(([k, v]) => <p key={k} className="hc-co-row"><span>{k}</span><strong className="nx-mono">{money(v)}</strong></p>)}</Card>
      </div>
      <Card title={t("co.vms")} flush>
        {data.vms.length === 0 ? <Empty icon={Wallet} title={t("co.noVm")} text="" /> : (
          <TableWrap label={t("co.vms")}>
            <table className="nx-table">
              <thead><tr><th scope="col">VM</th><th scope="col">{t("co.size")}</th><th scope="col">{t("pl.members")}</th><th scope="col">{t("pl.tags")}</th><th scope="col" className="nx-num">{t("co.month")}</th></tr></thead>
              <tbody>
                {data.vms.map((v) => (
                  <tr key={v.name}>
                    <th scope="row" className="nx-mono">{v.name} {!v.running && <Chip>{t("co.stopped")}</Chip>}</th>
                    <td className="nx-mono">{v.vcpu} vCPU · {Math.round(v.memory_mb / 1024)} GB · {v.disk_gb} GB</td>
                    <td>{v.pools.join(", ") || "—"}</td>
                    <td><TagChips tags={v.tags} /></td>
                    <td className="nx-num nx-mono">{money(v.monthly)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {admin && prices && (
        <Card title={t("co.prices")} note={t("co.pricesNote")}>
          <div className="nx-fg">
            <Field label={t("co.currency")}>{(p) => <input {...p} className="nx-inp" maxLength={8} value={prices.currency} onChange={(e) => setPrices((x) => ({ ...x, currency: e.target.value }))} />}</Field>
            {[["vcpu", "co.perVcpu"], ["memory_gb", "co.perGbRam"], ["disk_gb", "co.perGbDisk"]].map(([k, l]) => <Field key={k} label={t(l)}>{(p) => <input {...p} type="number" min="0" step="0.01" className="nx-inp" value={prices[k]} onChange={(e) => setPrices((x) => ({ ...x, [k]: e.target.value }))} />}</Field>)}
          </div>
          <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" onClick={() => act(() => saveCostPrices({ currency: prices.currency, vcpu: Number(prices.vcpu), memory_gb: Number(prices.memory_gb), disk_gb: Number(prices.disk_gb) }), t("gr.saved"))}>{t("gr.save")}</button></div>
        </Card>
      )}
      {budget && (
        <SideDrawer open title={t("co.newBudget")} onClose={() => setBudget(null)} footer={<>
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setBudget(null)}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" disabled={!budget.name.trim()} onClick={async () => { if (await act(() => saveBudget({ ...budget, monthly: Number(budget.monthly), alert_percent: Number(budget.alert_percent) }), t("gr.saved"))) setBudget(null); }}>{t("gr.save")}</button>
        </>}>
          <Field label={t("pl.name")}>{(p) => <input {...p} className="nx-inp" value={budget.name} onChange={(e) => setBudget((x) => ({ ...x, name: e.target.value }))} />}</Field>
          <Field label={t("sec.on")} hint={t("co.scopeHint")}>{(p) => <select {...p} className="nx-inp" value={budget.scope.startsWith("tag:") ? "tag" : budget.scope} onChange={(e) => setBudget((x) => ({ ...x, scope: e.target.value === "tag" ? "tag:" : e.target.value }))}><option value="cluster">{t("acc.wholeCluster")}</option>{pools.map((p) => <option key={p.id} value={`pool:${p.id}`}>{t("acc.kind.pool")} {p.name}</option>)}<option value="tag">{t("co.aTag")}</option></select>}</Field>
          {budget.scope.startsWith("tag:") && <Field label={t("co.aTag")}>{(p) => <input {...p} className="nx-inp nx-mono" value={budget.scope.slice(4)} onChange={(e) => setBudget((x) => ({ ...x, scope: `tag:${e.target.value.trim().toLowerCase()}` }))} />}</Field>}
          <div className="nx-fg">
            <Field label={t("co.monthly", { cur })}>{(p) => <input {...p} type="number" min="1" className="nx-inp" value={budget.monthly} onChange={(e) => setBudget((x) => ({ ...x, monthly: e.target.value }))} />}</Field>
            <Field label={t("co.alertAt")}>{(p) => <input {...p} type="number" min="1" max="100" className="nx-inp" value={budget.alert_percent} onChange={(e) => setBudget((x) => ({ ...x, alert_percent: e.target.value }))} />}</Field>
          </div>
        </SideDrawer>
      )}
    </>
  );
}
CostsPage.ownHeader = true;
