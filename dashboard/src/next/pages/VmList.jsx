import { useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ChevronDown, ChevronRight, Copy, Monitor, Play, Plus, Search, Square, SquareTerminal, TriangleAlert, X } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { capabilities, vmActionState } from "../lib/capabilities";
import { useVmActions } from "../lib/vmActions";
import { formatSizeMb, formatUptimeLong } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { PageHeader, Spark, Empty, StatePill, TableWrap, Meter, Chip } from "../components/ui";
import { useVmHistory } from "./VmPerformance";

const VIEW_KEY = "hyperlite-next-vmview";
const GROUP_KEY = "hyperlite-next-vmgroup";
const COLLAPSED_KEY = "hyperlite-next-vmcollapsed";
const NODE_PILLS_MAX = 6; // beyond this many nodes the filter becomes a select, pills would wrap into a wall
const PROBLEM = new Set(["plante", "bloque", "inconnu"]);
function readView() { try { return localStorage.getItem(VIEW_KEY) === "cards" ? "cards" : "table"; } catch { return "table"; } }
// Grouping by node: the user's choice when there is one, otherwise on (even with one node, the band tells which
// machine runs the VMs and how loaded it is).
function readGroup() { try { const v = localStorage.getItem(GROUP_KEY); return v == null ? null : v === "1"; } catch { return null; } }
function readCollapsed() { try { const v = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]"); return new Set(Array.isArray(v) ? v : []); } catch { return new Set(); } }
const pct = (used, total) => (used != null && total ? (used / total) * 100 : null);
const nodeAddress = (n) => n?.ip || n?.hostname || n?.live?.address || null;

// Detail panel of the selected VM: state, the contextual primary plus Stop, the last hour of CPU and memory
// (the VM's persisted history, this VM only), its address and resources.
function DetailPanel({ vm, onClose }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const { nodes, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, navigateTo: s.navigateTo })));
  const { run, openConsole } = useVmActions();
  const hist = useVmHistory(vm, "1h");
  const running = vm.etat === "actif";
  const cons = vmActionState("console", vm, caps);
  const start = vmActionState("start", vm, caps);
  const stop = vmActionState("stop", vm, caps);
  const rows = (hist.rows || []).slice(-60);
  const cpu = rows.map((r) => r.cpu_pct);
  const mem = rows.map((r) => (r.mem_total_mb ? (r.mem_used_mb / r.mem_total_mb) * 100 : null));
  const lastOf = (a) => [...a].reverse().find((v) => v != null);
  const btn = (state, label, onClick, cls = "nx-btn", Icon) => (
    <button type="button" className={cls} aria-disabled={!state.enabled || undefined} title={!state.enabled && state.reason ? t(state.reason) : undefined} onClick={() => state.enabled && onClick()}>
      {Icon && <Icon size={15} aria-hidden="true" />}{label}{!state.enabled && state.reason && <span className="nx-sr"> — {t(state.reason)}</span>}
    </button>
  );
  return (
    <aside className="nx-card2 nx-vmdetail" aria-label={t("vmlist.detailOf", { name: vm.nom })}>
      <div className="nx-vmdetail-h">
        <div className="nx-inline" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
          <span className="nx-otile" aria-hidden="true"><Monitor size={18} /></span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <h2>{vm.nom}</h2>
            <div className="nx-ometa"><StatePill kind="vm" wire={vm.etat} />{vm.os && <span>{vm.os}</span>}<span className="nx-sep" aria-hidden="true">·</span>
              <button type="button" className="nx-lnk" onClick={() => navigateTo("node", vm.node, "summary")}>{nodes.find((n) => n.id === vm.node)?.nom || vm.node}</button></div>
          </div>
          <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("vmlist.closeDetail")} onClick={onClose}><X size={15} aria-hidden="true" /></button>
        </div>
        <div className="nx-inline">
          {running ? btn(cons, t("actions.primary.console"), () => openConsole(vm), "nx-btn nx-btn--primary", SquareTerminal) : btn(start, t("menu.start"), () => run(vm, "start"), "nx-btn nx-btn--primary", Play)}
          {running ? btn(stop, t("menu.stop"), () => run(vm, "stop"), "nx-btn", Square) : null}
          <span className="nx-sp" />
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("vm", vm.nom, "summary")}>{t("vmlist.openPage")}<ChevronRight size={14} aria-hidden="true" /></button>
        </div>
      </div>
      {PROBLEM.has(vm.etat) && <div className="nx-bn" data-tone="warning" style={{ margin: "var(--space-3) var(--space-4) 0" }}><TriangleAlert size={16} aria-hidden="true" /><span className="nx-bn-t">{t(vm.etat === "plante" ? "vm.crashedHelp" : "vm.blockedHelp")}</span></div>}
      <div className="nx-vmdetail-s">
        <div className="nx-muted" style={{ fontSize: "var(--fs-12)", marginBottom: "var(--space-2)" }}>{t("vmlist.usage")}</div>
        {rows.length > 1 ? (
          <div className="nx-vmdetail-spark">
            <span>{t("ns.cpu")}</span><Spark data={cpu} /><span className="nx-mono">{lastOf(cpu) != null ? `${Math.round(lastOf(cpu))} %` : "—"}</span>
            <span>{t("ns.memory")}</span><Spark data={mem} /><span className="nx-mono">{lastOf(mem) != null ? `${Math.round(lastOf(mem))} %` : "—"}</span>
          </div>
        ) : <span className="nx-muted" style={{ fontSize: "var(--fs-125)" }}>{running ? t("ns.collecting") : t("vmlist.noMeasure")}</span>}
      </div>
      <div className="nx-vmdetail-s" style={{ borderBottom: 0 }}>
        <dl className="nx-dl2" style={{ gridTemplateColumns: "6.6667rem minmax(0,1fr)" }}>
          <dt>{t("vmlist.ip")}</dt><dd>{vm.ip ? <><span className="nx-mono">{vm.ip}</span><button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("menu.copyIp")} onClick={() => navigator.clipboard?.writeText(vm.ip)}><Copy size={14} aria-hidden="true" /></button></> : <span className="nx-muted" title={t("ns.ipHelp")}>{t("vm.ipNone")}</span>}</dd>
          <dt>{t("vmlist.resources")}</dt><dd className="nx-mono">{vm.vcpu} vCPU · {formatSizeMb(vm.memoire_mo, lang)}</dd>
          <dt>{t("ns.col.uptime")}</dt><dd className="nx-mono">{running ? formatUptimeLong(vm.uptime_s, lang) || "—" : "—"}</dd>
        </dl>
      </div>
    </aside>
  );
}

// Head of a node's group: which machine runs these VMs and whether it is healthy (state, CPU and memory of the
// host, VMs to check), with a toggle to fold the group. In the table it stays stuck under the column headers
// while its VMs scroll by.
function NodeBand({ node, list, collapsed, onToggle }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const known = node.id !== "?";
  const online = node.etat === "online";
  const run = list.filter((v) => v.etat === "actif").length;
  const toCheck = list.filter((v) => PROBLEM.has(v.etat)).length;
  const addr = nodeAddress(node);
  return (
    <span className="nx-nodeband">
      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon nx-chev" aria-expanded={!collapsed}
        aria-label={t(collapsed ? "vmlist.expandNode" : "vmlist.collapseNode", { name: node.nom })} onClick={onToggle}>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <span className="nx-nodeband-id">
        {online && <StatusIndicator kind="node" wire={node.etat} compact />}
        {known ? <button type="button" className="nx-lnk" onClick={() => navigateTo("node", node.id, "summary")}>{node.nom}</button> : <span>{node.nom}</span>}
        {addr && <span className="nx-mono nx-muted nx-nodeband-addr">{addr}</span>}
        {known && node.etat && !online && <StatePill kind="node" wire={node.etat} />}
        {toCheck > 0 && <Chip tone="warning">{t("vmlist.band.toCheck", { n: toCheck })}</Chip>}
      </span>
      <span className="nx-nodeband-stats">
        {known && <span className="nx-nodeband-m"><span className="nx-muted">{t("vmlist.band.cpu")}</span><Meter value={node.cpu_utilisation} label={`${t("ns.cpu")} ${node.nom}`} /></span>}
        {known && <span className="nx-nodeband-m"><span className="nx-muted">{t("vmlist.band.ram")}</span><Meter value={pct(node.memoire_utilisee_mo, node.memoire_totale_mo)} label={`${t("ns.memory")} ${node.nom}`} /></span>}
        <span className="nx-mono nx-muted nx-nodeband-count">{t("vmlist.groupCount", { run, n: list.length })}</span>
        <span className="nx-mono nx-muted nx-nodeband-count--short" aria-hidden="true">{t("vmlist.band.runShort", { run, n: list.length })}</span>
      </span>
    </span>
  );
}

// "Virtual machines": every VM of every node, problems first, with a detail panel for the selected row.
export default function VmList() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { vms, nodes, navigateTo } = useInfraStore(useShallow((s) => ({ vms: s.vms, nodes: s.nodes, navigateTo: s.navigateTo })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const [view, setViewState] = useState(readView);
  const [q, setQ] = useState("");
  const [chip, setChip] = useState("all");
  const [sort, setSort] = useState({ key: "state", dir: 1 });
  const [selName, setSelName] = useState(null);
  const [detail, setDetail] = useState(true);
  const [nodeFilter, setNodeFilter] = useState("");
  const [groupPref, setGroupPref] = useState(readGroup);
  const group = groupPref ?? true;
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleNode = (id) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next])); } catch { /* preference only */ }
    return next;
  });
  const setGroup = (v) => { setGroupPref(v); try { localStorage.setItem(GROUP_KEY, v ? "1" : "0"); } catch { /* preference only */ } };
  const setView = (v) => { setViewState(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* preference only */ } };
  const nodeName = (id) => nodes.find((n) => n.id === id)?.nom || id;

  const counts = useMemo(() => ({
    all: vms.length,
    running: vms.filter((v) => v.etat === "actif").length,
    stopped: vms.filter((v) => v.etat === "arrete" || v.etat === "en_arret").length,
    problems: vms.filter((v) => PROBLEM.has(v.etat)).length,
  }), [vms]);
  const problems = vms.filter((v) => PROBLEM.has(v.etat));

  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    const list = vms.filter((v) => (!nodeFilter || v.node === nodeFilter) && (chip === "all" || (chip === "running" && v.etat === "actif") || (chip === "stopped" && (v.etat === "arrete" || v.etat === "en_arret")) || (chip === "problems" && PROBLEM.has(v.etat)))
      && (!n || `${v.nom} ${v.ip || ""} ${v.os || ""} ${nodeName(v.node)}`.toLowerCase().includes(n)));
    const rank = (v) => (PROBLEM.has(v.etat) ? 0 : v.etat === "actif" ? 1 : 2); // problems first
    const val = { state: rank, name: (v) => v.nom.toLowerCase(), node: (v) => nodeName(v.node).toLowerCase(), res: (v) => (v.vcpu || 0) * 1e6 + (v.memoire_mo || 0), uptime: (v) => v.uptime_s || 0 }[sort.key];
    return [...list].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : a.nom.localeCompare(b.nom)) * sort.dir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vms, q, chip, sort, nodes, nodeFilter]);

  // One section per node (the machine that runs the VMs), in the order of the nodes page; a node without VMs is
  // shown too when nothing is filtered, so every machine appears.
  const filtering = chip !== "all" || q.trim() !== "";
  const groups = nodes
    .filter((n) => !nodeFilter || n.id === nodeFilter)
    .map((n) => ({ node: n, vms: shown.filter((v) => v.node === n.id) }))
    .filter((g) => g.vms.length > 0 || !filtering);
  const orphans = shown.filter((v) => !nodes.some((n) => n.id === v.node));
  if (orphans.length) groups.push({ node: { id: "?", nom: t("vmlist.unknownNode") }, vms: orphans });

  const selected = shown.find((v) => v.nom === selName) || (detail ? shown[0] : null);
  const runningVms = vms.filter((v) => v.etat === "actif");
  const vcpu = runningVms.reduce((a, v) => a + (v.vcpu || 0), 0);
  const mem = runningVms.reduce((a, v) => a + (v.memoire_mo || 0), 0);
  const select = (v) => { setSelName(v.nom); setDetail(true); };
  const th = (key, label, cls) => (
    <th scope="col" className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="nx-thbtn" onClick={() => setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }))}>{label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</button>
    </th>
  );
  const sub = (v) => (PROBLEM.has(v.etat) ? <small className="is-warn">{t(`vmlist.reason.${v.etat}`)}</small> : v.os ? <small>{v.os}</small> : null);
  const showDetail = detail && selected && view === "table";
  const nodeCell = (id) => {
    const n = nodes.find((x) => x.id === id);
    return n ? <button type="button" className="nx-lnk nx-mono" title={nodeAddress(n) || undefined} onClick={(e) => { e.stopPropagation(); navigateTo("node", n.id, "summary"); }}>{n.nom}</button> : <span className="nx-mono">{id}</span>;
  };
  const renderCard = (vm) => (
              <article key={`${vm.node}:${vm.nom}`} className="nx-card2 nx-vmcard2" aria-label={vm.nom}>
                <div className="nx-inline"><StatusIndicator kind="vm" wire={vm.etat} compact /><button type="button" className="nx-lnk" onClick={() => navigateTo("vm", vm.nom, "summary")}>{vm.nom}</button></div>
                <div className="nx-muted" style={{ fontSize: "var(--fs-12)" }}>{PROBLEM.has(vm.etat) ? <span className="nx-tone-warning">{t(`vmlist.reason.${vm.etat}`)}</span> : vm.os || "—"}</div>
                <dl className="nx-dl2" style={{ gridTemplateColumns: "5.3333rem minmax(0,1fr)", marginTop: "var(--space-2)" }}>
                  <dt>{t("ns.node")}</dt><dd className="nx-mono">{nodeName(vm.node)}</dd>
                  <dt>IP</dt><dd className="nx-mono">{vm.ip || "—"}</dd>
                  <dt>{t("vmlist.resources")}</dt><dd className="nx-mono">{vm.vcpu} · {formatSizeMb(vm.memoire_mo, lang)}</dd>
                </dl>
              </article>
            );
  const renderRow = (vm, withNode) => {
                      const sel = showDetail && selected?.nom === vm.nom;
                      return (
                        <tr key={`${vm.node}:${vm.nom}`} className={`nx-rowlink${sel ? " is-sel" : PROBLEM.has(vm.etat) ? " is-warn" : ""}`} aria-selected={sel || undefined} tabIndex={0}
                          onClick={() => select(vm)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(vm); } }}>
                          <td style={{ width: "2rem" }}><StatusIndicator kind="vm" wire={vm.etat} compact /></td>
                          <th scope="row" className="nx-nm"><button type="button" className="nx-lnk" onClick={(e) => { e.stopPropagation(); navigateTo("vm", vm.nom, "summary"); }}>{vm.nom}</button>{sub(vm)}</th>
                          {withNode && <td>{nodeCell(vm.node)}</td>}
                          <td className="nx-mono">{vm.ip || <span className="nx-muted">—</span>}</td>
                          <td className="nx-num nx-mono">{vm.vcpu} · {formatSizeMb(vm.memoire_mo, lang)}</td>
                          <td className="nx-mono nx-muted">{vm.etat === "actif" ? formatUptimeLong(vm.uptime_s, lang) || "—" : "—"}</td>
                        </tr>
                      );
                    };

  return (
    <>
      <PageHeader title={t("tab.vms")} count={vms.length}
        actions={caps.create && <button type="button" className="nx-btn nx-btn--primary" onClick={() => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" }))}><Plus size={15} aria-hidden="true" />{t("vmlist.create")}</button>} />
      {problems.length > 0 && (
        <div className="nx-bn" data-tone="warning" role="status"><TriangleAlert size={16} aria-hidden="true" />
          <span className="nx-bn-t"><b>{t("ov.attention", { n: problems.length })}</b> : {problems.slice(0, 4).map((v) => v.nom).join(", ")}</span>
          <button type="button" className="nx-btn nx-btn--sm" onClick={() => setChip("problems")}>{t("vmlist.show")}</button></div>
      )}
      <div className="nx-bar nx-bar--rule">
        <div className="nx-seg2" role="group" aria-label={t("vmlist.filterState")}>
          {[["all", t("vmlist.all")], ["running", t("state.running")], ["stopped", t("state.stopped")], ["problems", t("vmlist.problems")]].map(([id, label]) => (
            <button key={id} type="button" aria-pressed={chip === id} onClick={() => setChip(id)}>{label} <span className={`nx-n${id === "problems" && counts.problems ? " is-warn" : ""}`}>{counts[id]}</span></button>
          ))}
        </div>
        {nodes.length > 1 && nodes.length <= NODE_PILLS_MAX && (
          <div className="nx-seg2 nx-seg2--scroll" role="group" aria-label={t("vmlist.filterNode")}>
            <button type="button" aria-pressed={!nodeFilter} onClick={() => setNodeFilter("")}>{t("vmlist.allNodes")} <span className="nx-n">{vms.length}</span></button>
            {nodes.map((n) => (
              <button key={n.id} type="button" aria-pressed={nodeFilter === n.id} onClick={() => setNodeFilter(n.id)}>
                <StatusIndicator kind="node" wire={n.etat} compact />{n.nom} <span className="nx-n">{vms.filter((v) => v.node === n.id).length}</span>
              </button>
            ))}
          </div>
        )}
        <span className="nx-sp" />
        {nodes.length > NODE_PILLS_MAX && (
          <select className="nx-sel" aria-label={t("vmlist.filterNode")} value={nodeFilter} onChange={(e) => setNodeFilter(e.target.value)}>
            <option value="">{t("vmlist.allNodes")}</option>
            {nodes.map((n) => <option key={n.id} value={n.id}>{n.nom} ({vms.filter((v) => v.node === n.id).length})</option>)}
          </select>
        )}
        <button type="button" className="nx-btn nx-btn--sm" aria-pressed={group} onClick={() => setGroup(!group)}>{t("vmlist.groupByNode")}</button>
        <label className="nx-search2"><Search size={15} aria-hidden="true" /><input type="search" aria-label={t("ns.filterVms")} placeholder={t("vmlist.searchPh")} value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <div className="nx-seg2" role="group" aria-label={t("ns.viewMode")}>
          <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>{t("ns.table")}</button>
          <button type="button" aria-pressed={view === "cards"} onClick={() => setView("cards")}>{t("ns.cards")}</button>
        </div>
      </div>

      {vms.length === 0 ? (
        <div className="nx-card2"><Empty icon={Monitor} title={t("ns.noVms")} text={t("vmlist.noneHelp")} action={caps.create && <button type="button" className="nx-btn" onClick={() => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" }))}><Plus size={15} aria-hidden="true" />{t("vmlist.create")}</button>} /></div>
      ) : view === "cards" ? (
        shown.length === 0 ? <p className="nx-muted" role="status">{t("act.noneFiltered")}</p> : (
          group ? (
            <div className="nx-stack">
              {groups.map(({ node, vms: list }) => (
                <section key={node.id} aria-label={node.nom}>
                  <h2 className="nx-grouphead nx-grouphead--cards"><NodeBand node={node} list={list} collapsed={collapsed.has(node.id)} onToggle={() => toggleNode(node.id)} /></h2>
                  {collapsed.has(node.id) ? null : list.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("ns.noVms")}</p> : <div className="nx-cards2">{list.map((vm) => renderCard(vm))}</div>}
                </section>
              ))}
            </div>
          ) : <div className="nx-cards2">{shown.map((vm) => renderCard(vm))}</div>
        )
      ) : (
        <div className={`nx-vmgrid${showDetail ? " has-detail" : ""}`}>
          <div className="nx-card2 nx-card2--flush nx-card2--sticky">
            {shown.length === 0 ? <p className="nx-muted" role="status" style={{ padding: "var(--space-4)", margin: 0 }}>{t("act.noneFiltered")}</p> : (
              <TableWrap sticky>
                <table className="nx-table">
                  <thead><tr>{th("state", <span className="nx-sr">{t("ns.col.state")}</span>)}{th("name", t("ns.col.name"))}{!group && th("node", t("ns.node"))}<th scope="col">{t("vmlist.ip")}</th>{th("res", t("vmlist.cpuMem"), "nx-num")}{th("uptime", t("ns.col.uptime"))}</tr></thead>
                  {group ? groups.map(({ node, vms: list }) => {
                    const folded = collapsed.has(node.id);
                    return (
                      <tbody key={node.id} className="nx-group">
                        <tr className="nx-grouprow nx-grouprow--band">
                          <th scope="colgroup" colSpan={5}><NodeBand node={node} list={list} collapsed={folded} onToggle={() => toggleNode(node.id)} /></th>
                        </tr>
                        {folded ? null : list.length === 0 ? <tr><td colSpan={5} className="nx-muted" style={{ paddingLeft: "2.6667rem" }}>{t("ns.noVms")}</td></tr> : list.map((vm) => renderRow(vm, false))}
                      </tbody>
                    );
                  }) : <tbody>{shown.map((vm) => renderRow(vm, true))}</tbody>}
                  <tfoot><tr><td colSpan={group ? 5 : 6}>{t("vmlist.footer", { shown: shown.length, total: vms.length, vcpu, mem: formatSizeMb(mem, lang) })}</td></tr></tfoot>
                </table>
              </TableWrap>
            )}
          </div>
          {showDetail && <DetailPanel vm={selected} onClose={() => { setDetail(false); setSelName(null); }} />}
        </div>
      )}
    </>
  );
}
VmList.ownHeader = true;
