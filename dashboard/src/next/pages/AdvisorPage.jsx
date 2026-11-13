import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, Download, Eye, EyeOff, Gauge, Leaf, Lightbulb, RefreshCw, Search, ShieldCheck, Wrench, LifeBuoy } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { requestIntent } from "../lib/intents";
import { CATEGORIES, IMPACTS, LIMITS, forResource, isHidden } from "../lib/advisor";
import { useAdvisor, useHiddenState } from "../lib/useAdvisor";
import { PageHeader, Empty, TableWrap } from "../components/ui";
import { Cmd, FilterPill, downloadCsv } from "../components/portal";

const ICON = { reliability: LifeBuoy, security: ShieldCheck, performance: Gauge, efficiency: Leaf, operations: Wrench };
const SNOOZE_DAYS = 7;

function Impact({ level }) {
  const t = useT();
  return <span className="hc-impact" data-level={level}>{t(`adv.impact.${level}`)}</span>;
}

// One affected resource, as a link to where it is managed.
function ResourceLink({ r }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const go = r.type === "vm" || r.type === "node" ? () => navigateTo(r.type, r.id, "summary")
    : r.type === "pool" ? () => navigateTo("datacenter", null, "storage")
      : r.type === "user" ? () => navigateTo("datacenter", null, "permissions")
        : r.type === "token" ? () => navigateTo("datacenter", null, "api") : null;
  const label = r.type === "cluster" ? t("adv.res.cluster") : r.name;
  return (
    <li>
      <span className="nx-muted">{t(`adv.res.${r.type}`)}</span>
      {go ? <button type="button" className="nx-lnk" onClick={go}>{label}</button> : <span>{label}</span>}
      {r.value != null && <span className="nx-muted nx-mono">{r.type === "node" && r.id && t("adv.val.ratio", { v: r.value })}{r.type === "pool" && `${r.value} %`}</span>}
    </li>
  );
}

// The rows of recommendations: impact, what to do, category, how many resources; a row opens on its detail.
function RecTable({ list, hidden, setHidden, showHidden }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const [open, setOpen] = useState(null);
  const fix = (r) => {
    if (r.go) { if (r.go.intent) requestIntent(r.go.intent); navigateTo("datacenter", null, r.go.svc); return; }
    const first = r.resources[0];
    if (first) navigateTo(first.type, first.id, "summary");
  };
  return (
    <TableWrap>
      <table className="nx-table hc-grid--list hc-adv">
        <thead><tr><th scope="col">{t("adv.col.impact")}</th><th scope="col">{t("adv.col.rec")}</th><th scope="col">{t("adv.col.category")}</th><th scope="col" className="nx-num">{t("adv.col.resources")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
        <tbody>
          {list.map((r) => {
            const off = isHidden(hidden, r.id);
            const Icon = ICON[r.category];
            return (
              <Fragment key={r.id}>
                <tr data-off={off || undefined}>
                  <td><Impact level={r.impact} /></td>
                  <th scope="row" className="hc-adv-t">
                    <button type="button" className="hc-adv-open" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
                      {open === r.id ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                      <span>{t(`adv.${r.id}`)}</span>
                    </button>
                    {off && showHidden && <small className="nx-muted">{hidden[r.id] === "dismissed" ? t("adv.dismissed") : t("adv.snoozed")}</small>}
                  </th>
                  <td><span className="hc-adv-cat"><Icon size={14} aria-hidden="true" />{t(`adv.cat.${r.category}`)}</span></td>
                  <td className="nx-num">{r.id === "no-channel" ? "—" : r.resources.length}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--sm" onClick={() => fix(r)}>{t("adv.fix")}</button>
                    {off ? <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setHidden(r.id, null)}>{t("adv.restore")}</button> : <>
                      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" title={t("adv.snoozeHelp", { n: SNOOZE_DAYS })} onClick={() => setHidden(r.id, Date.now() + SNOOZE_DAYS * 86_400_000)}>{t("adv.snooze")}</button>
                      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setHidden(r.id, "dismissed")}>{t("adv.dismiss")}</button>
                    </>}
                  </div></td>
                </tr>
                {open === r.id && (
                  <tr><td colSpan={5} className="nx-detailcell">
                    <div className="hc-adv-d">
                      <div><h3>{t("adv.problem")}</h3><p>{t(`adv.${r.id}.d`, LIMITS)}</p></div>
                      <div><h3>{t("adv.solution")}</h3><p>{t(`adv.${r.id}.fix`, LIMITS)}</p></div>
                      {r.id !== "no-channel" && <div><h3>{t("adv.affected", { n: r.resources.length })}</h3><ul className="hc-adv-res">{r.resources.slice(0, 50).map((x) => <ResourceLink key={`${x.type}:${x.id}`} r={x} />)}</ul>
                        {r.resources.length > 50 && <p className="nx-muted">{t("adv.more", { n: r.resources.length - 50 })}</p>}</div>}
                    </div>
                  </td></tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}

// Recommendations (Advisor): what to fix in the cluster, by category and impact, each with the resources it concerns.
export default function AdvisorPage() {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const { list, refresh } = useAdvisor(caps.admin);
  const [hidden, setHidden] = useHiddenState();
  const [showHidden, setShowHidden] = useState(false);
  const [cat, setCat] = useState("");
  const [impact, setImpact] = useState("");
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const all = list || [];
  const active = all.filter((r) => !isHidden(hidden, r.id));
  const shown = (showHidden ? all : active).filter((r) => (!cat || r.category === cat) && (!impact || r.impact === impact)
    && (!needle || `${t(`adv.${r.id}`)} ${r.resources.map((x) => x.name).join(" ")}`.toLowerCase().includes(needle)));
  const hiddenCount = all.length - active.length;
  const exportCsv = () => downloadCsv("recommendations.csv", [
    [t("adv.col.impact"), t("adv.col.rec"), t("adv.col.category"), t("adv.col.resources")],
    ...shown.flatMap((r) => r.resources.map((x) => [t(`adv.impact.${r.impact}`), t(`adv.${r.id}`), t(`adv.cat.${r.category}`), x.name || t("adv.res.cluster")])),
  ]);

  return (
    <>
      <PageHeader title={t("tab.advisor")} desc={t("adv.desc")} actions={(
        <>
          <Cmd icon={RefreshCw} label={t("action.refresh")} onClick={refresh} />
          <Cmd icon={Download} label={t("list.exportCsv")} onClick={exportCsv} disabled={shown.length === 0} />
          <Cmd icon={showHidden ? EyeOff : Eye} label={showHidden ? t("adv.hideHidden") : t("adv.showHidden", { n: hiddenCount })} onClick={() => setShowHidden(!showHidden)} disabled={!showHidden && hiddenCount === 0} aria-pressed={showHidden} />
        </>
      )} />
      <div className="hc-advcats" role="group" aria-label={t("adv.col.category")}>
        {CATEGORIES.map((c) => {
          const mine = active.filter((r) => r.category === c);
          const Icon = ICON[c];
          return (
            <button key={c} type="button" className="hc-advcat" aria-pressed={cat === c} onClick={() => setCat(cat === c ? "" : c)}>
              <span className="hc-advcat-h"><Icon size={18} aria-hidden="true" /><b>{t(`adv.cat.${c}`)}</b></span>
              <span className="hc-advcat-n">{list ? mine.length : "…"}</span>
              <small>{!list ? t("loading") : mine.length === 0 ? t("adv.catNone") : IMPACTS.map((i) => [i, mine.filter((r) => r.impact === i).length]).filter(([, n]) => n).map(([i, n]) => t(`adv.nImpact.${i}`, { n })).join(" · ")}</small>
            </button>
          );
        })}
      </div>
      {list && all.length > 0 && (
        <>
          <div className="hc-fbar">
            <label className="hc-filter hc-filter--wide"><Search size={16} aria-hidden="true" /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("list.filterAny")} aria-label={t("list.filterAny")} /></label>
            <FilterPill label={t("adv.col.category")} value={cat} onChange={setCat} options={[{ value: "", label: t("list.all") }, ...CATEGORIES.map((c) => ({ value: c, label: t(`adv.cat.${c}`) }))]} />
            <FilterPill label={t("adv.col.impact")} value={impact} onChange={setImpact} options={[{ value: "", label: t("list.all") }, ...IMPACTS.map((i) => ({ value: i, label: t(`adv.impact.${i}`) }))]} />
          </div>
          <p className="hc-count" role="status">{t("list.showing", { n: shown.length, total: showHidden ? all.length : active.length })}</p>
        </>
      )}
      <div className="nx-card2 nx-card2--flush">
        {!list ? <p className="nx-muted" style={{ padding: "var(--space-4)" }}>{t("loading")}</p>
          : shown.length === 0 ? <Empty icon={Lightbulb} title={active.length === 0 ? t("adv.none") : t("act.noneFiltered")} text={active.length === 0 ? t("adv.noneHelp") : undefined} />
            : <RecTable list={shown} hidden={hidden} setHidden={setHidden} showHidden={showHidden} />}
      </div>
    </>
  );
}
AdvisorPage.ownHeader = true;

// The recommendations of one VM or node, on its own page.
function ResourceAdvisor({ type, id }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const { list } = useAdvisor(caps.admin);
  const [hidden, setHidden] = useHiddenState();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  if (!list) return <p className="nx-muted">{t("loading")}</p>;
  // Each recommendation narrowed to this resource: its count and its detail speak of this one only.
  const mine = forResource(list, type, id).map((r) => ({ ...r, resources: r.resources.filter((x) => x.type === type && x.id === id) }));
  return (
    <>
      <p className="nx-ph-desc">{t("adv.resDesc")} <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "advisor")}>{t("adv.seeAll")}</button></p>
      <div className="nx-card2 nx-card2--flush">
        {mine.length === 0 ? <Empty icon={Lightbulb} title={t("adv.noneHere")} /> : <RecTable list={mine} hidden={hidden} setHidden={setHidden} showHidden />}
      </div>
    </>
  );
}
export function VmAdvisorPage({ resource: vm }) {
  return <ResourceAdvisor type="vm" id={vm.node && vm.node !== "local" ? `${vm.name}@${vm.node}` : vm.name} />;
}
export function NodeAdvisorPage({ resource: node }) {
  return <ResourceAdvisor type="node" id={node.id} />;
}
