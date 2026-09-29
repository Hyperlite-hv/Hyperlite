import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ChevronDown, ChevronRight, Columns3, Copy, Monitor, Play, Plus, Search, Square, SquareTerminal, Tag, TriangleAlert, Users, X } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { vmKey } from "../lib/vmId";
import { capabilities, vmActionState } from "../lib/capabilities";
import { useVmActions } from "../lib/vmActions";
import { formatSizeMb, formatUptimeLong } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { useContextTarget } from "../components/ContextMenu";
import { VmContextMenu, NodeContextMenu } from "../components/ObjectActions";
import { PageHeader, Spark, Empty, StatePill, TableWrap, Meter, Chip } from "../components/ui";
import { useVmHistory } from "./VmPerformance";
import BulkBar from "../components/BulkBar";
import { TagChips } from "../components/NotesCard";
import { allTags, metaOf, useMetaStore } from "../lib/meta";
import { OPTIONAL_COLUMNS, deleteView, groupVms, readColumns, readGroupMode, readSavedViews, saveColumns, saveGroupMode, saveView } from "../lib/vmViews";
import { fetchPools } from "../../api/client";
import { promptText } from "../../store/usePromptStore";
import { confirmAction } from "../../store/useConfirmStore";

const VIEW_KEY = "hyperlite-next-vmview";
const COLLAPSED_KEY = "hyperlite-next-vmcollapsed";
const NODE_PILLS_MAX = 6; // beyond this many nodes the filter becomes a select, pills would wrap into a wall
const PROBLEM = new Set(["plante", "bloque", "inconnu"]);
function readView() { try { return localStorage.getItem(VIEW_KEY) === "cards" ? "cards" : "table"; } catch { return "table"; } }
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
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("vm", vmKey(vm), "summary")}>{t("vmlist.openPage")}<ChevronRight size={14} aria-hidden="true" /></button>
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

// Head of a tag's or a pool's group: its name, how many of its VMs run, and a toggle to fold it.
function LabelBand({ group, collapsed, onToggle }) {
  const t = useT();
  const Icon = group.kind === "tag" ? Tag : Users;
  const run = group.vms.filter((v) => v.etat === "actif").length;
  return (
    <span className="nx-nodeband">
      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon nx-chev" aria-expanded={!collapsed}
        aria-label={t(collapsed ? "vmlist.expandGroup" : "vmlist.collapseGroup", { name: group.label })} onClick={onToggle}>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <span className="nx-nodeband-id"><Icon size={14} aria-hidden="true" /><strong className={group.none ? "nx-muted" : undefined}>{group.label}</strong></span>
      <span className="nx-nodeband-stats">
        <span className="nx-mono nx-muted nx-nodeband-count">{t("vmlist.groupCount", { run, n: group.vms.length })}</span>
        <span className="nx-mono nx-muted nx-nodeband-count--short" aria-hidden="true">{t("vmlist.band.runShort", { run, n: group.vms.length })}</span>
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
  const [tagFilter, setTagFilter] = useState("");
  const { byKey: metaByKey, load: loadMeta } = useMetaStore(useShallow((s) => ({ byKey: s.byKey, load: s.load })));
  useEffect(() => { loadMeta(); }, [loadMeta]);
  const tagsOf = (v) => metaOf(metaByKey, "vm", v.node, v.nom)?.tags || [];
  const tagList = allTags(metaByKey, "vm");
  const [groupMode, setGroupModeState] = useState(readGroupMode);
  const [columns, setColumnsState] = useState(readColumns);
  const [savedViews, setSavedViews] = useState(readSavedViews);
  const [currentView, setCurrentView] = useState("");
  const [pools, setPools] = useState([]);
  useEffect(() => { if (caps.admin) fetchPools().then((p) => setPools(Array.isArray(p) ? p : [])).catch(() => setPools([])); }, [caps.admin]);
  // Grouping by pool needs the pools, which only administrators read.
  const mode = groupMode === "pool" && !caps.admin ? "node" : groupMode;
  const group = mode !== "none";
  const setGroupMode = (m) => { setGroupModeState(m); saveGroupMode(m); };
  const setColumns = (c) => { setColumnsState(c); saveColumns(c); };
  const col = (c) => columns.includes(c) && !(c === "node" && mode === "node");
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleNode = (id) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next])); } catch { /* preference only */ }
    return next;
  });
  const viewState = () => ({ chip, nodeFilter, tagFilter, q, groupMode, columns, sort });
  function applyView(name) {
    setCurrentView(name);
    const v = savedViews.find((x) => x.name === name);
    if (!v) return;
    const st = v.state;
    setChip(st.chip ?? "all"); setNodeFilter(st.nodeFilter ?? ""); setTagFilter(st.tagFilter ?? ""); setQ(st.q ?? "");
    if (st.groupMode) setGroupMode(st.groupMode);
    if (st.columns) setColumns(st.columns);
    if (st.sort) setSort(st.sort);
  }
  async function saveCurrentView() {
    const name = await promptText({ title: t("vmlist.saveViewTitle"), label: t("vmlist.viewName"), defaultValue: currentView, confirmLabel: t("vmlist.saveView"),
      validate: (v) => (!v.trim() ? t("vmlist.viewNameRule") : "") });
    if (!name) return;
    setSavedViews((views) => saveView(views, name, viewState()));
    setCurrentView(name.trim().slice(0, 40));
  }
  async function removeCurrentView() {
    if (!currentView || !(await confirmAction({ title: t("vmlist.deleteViewTitle", { name: currentView }), message: t("vmlist.deleteViewMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    setSavedViews((views) => deleteView(views, currentView));
    setCurrentView("");
  }
  const setView = (v) => { setViewState(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* preference only */ } };
  const nodeName = (id) => nodes.find((n) => n.id === id)?.nom || id;
  const ctx = useContextTarget(); // right click on a VM or a node band: its actions
  // Selection for bulk actions, by VM identity (node and name): a VM that disappears leaves the selection.
  const selectable = caps.power;
  const [picked, setPicked] = useState(() => new Set());
  const pickedVms = selectable ? vms.filter((v) => picked.has(vmKey(v))) : [];
  const togglePick = (vm) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(vmKey(vm))) next.delete(vmKey(vm)); else next.add(vmKey(vm));
    return next;
  });
  const clearPicks = () => setPicked(new Set());

  const counts = useMemo(() => ({
    all: vms.length,
    running: vms.filter((v) => v.etat === "actif").length,
    stopped: vms.filter((v) => v.etat === "arrete" || v.etat === "en_arret").length,
    problems: vms.filter((v) => PROBLEM.has(v.etat)).length,
  }), [vms]);
  const problems = vms.filter((v) => PROBLEM.has(v.etat));

  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    const list = vms.filter((v) => (!nodeFilter || v.node === nodeFilter) && (!tagFilter || tagsOf(v).includes(tagFilter)) && (chip === "all" || (chip === "running" && v.etat === "actif") || (chip === "stopped" && (v.etat === "arrete" || v.etat === "en_arret")) || (chip === "problems" && PROBLEM.has(v.etat)))
      && (!n || `${v.nom} ${v.ip || ""} ${v.os || ""} ${nodeName(v.node)} ${tagsOf(v).join(" ")}`.toLowerCase().includes(n)));
    const rank = (v) => (PROBLEM.has(v.etat) ? 0 : v.etat === "actif" ? 1 : 2); // problems first
    const val = { state: rank, name: (v) => v.nom.toLowerCase(), node: (v) => nodeName(v.node).toLowerCase(), res: (v) => (v.vcpu || 0) * 1e6 + (v.memoire_mo || 0), uptime: (v) => v.uptime_s || 0 }[sort.key];
    return [...list].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : a.nom.localeCompare(b.nom)) * sort.dir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vms, q, chip, sort, nodes, nodeFilter, tagFilter, metaByKey]);

  const shownPicked = shown.filter((v) => picked.has(vmKey(v))).length;
  const pickAllShown = () => setPicked((prev) => {
    const next = new Set(prev);
    if (shownPicked === shown.length) shown.forEach((v) => next.delete(vmKey(v)));
    else shown.forEach((v) => next.add(vmKey(v)));
    return next;
  });
  const pickBox = (vm) => (
    <input type="checkbox" className="nx-pick" checked={picked.has(vmKey(vm))} aria-label={t("bulk.pick", { name: vm.nom })}
      onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} onChange={() => togglePick(vm)} />
  );

  // One section per node (the machine that runs the VMs), in the order of the nodes page; a node without VMs is
  // shown too when nothing is filtered, so every machine appears.
  const filtering = chip !== "all" || q.trim() !== "" || tagFilter !== "";
  const groups = groupVms(shown, mode, {
    nodes: nodes.filter((n) => !nodeFilter || n.id === nodeFilter), tagsOf, pools, withEmpty: !filtering,
    noneLabel: t(mode === "node" ? "vmlist.unknownNode" : mode === "tag" ? "vmlist.noTag" : "vmlist.noPool"),
  }) || [];
  const band = (g, folded) => (g.kind === "node"
    ? <NodeBand node={g.node} list={g.vms} collapsed={folded} onToggle={() => toggleNode(g.key)} />
    : <LabelBand group={g} collapsed={folded} onToggle={() => toggleNode(g.key)} />);
  const colCount = 2 + OPTIONAL_COLUMNS.filter(col).length + (selectable ? 1 : 0);

  const selected = shown.find((v) => vmKey(v) === selName) || (detail ? shown[0] : null);
  const runningVms = vms.filter((v) => v.etat === "actif");
  const vcpu = runningVms.reduce((a, v) => a + (v.vcpu || 0), 0);
  const mem = runningVms.reduce((a, v) => a + (v.memoire_mo || 0), 0);
  const select = (v) => { setSelName(vmKey(v)); setDetail(true); };
  const th = (key, label, cls) => (
    <th scope="col" className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="nx-thbtn" onClick={() => setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }))}>{label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</button>
    </th>
  );
  const sub = (v) => (
    <>
      {PROBLEM.has(v.etat) ? <small className="is-warn">{t(`vmlist.reason.${v.etat}`)}</small> : v.os ? <small>{v.os}</small> : null}
      <TagChips tags={tagsOf(v)} onTag={setTagFilter} />
    </>
  );
  const showDetail = detail && selected && view === "table";
  const nodeCell = (id) => {
    const n = nodes.find((x) => x.id === id);
    return n ? <button type="button" className="nx-lnk nx-mono" title={nodeAddress(n) || undefined} onClick={(e) => { e.stopPropagation(); navigateTo("node", n.id, "summary"); }}>{n.nom}</button> : <span className="nx-mono">{id}</span>;
  };
  const renderCard = (vm) => (
              <article key={`${vm.node}:${vm.nom}`} className={`nx-card2 nx-vmcard2${ctx.is("vm", vmKey(vm)) ? " is-ctx" : ""}`} aria-label={vm.nom} onContextMenu={ctx.open("vm", vm, vmKey(vm))}>
                <div className="nx-inline">{selectable && pickBox(vm)}<StatusIndicator kind="vm" wire={vm.etat} compact /><button type="button" className="nx-lnk" onClick={() => navigateTo("vm", vmKey(vm), "summary")}>{vm.nom}</button></div>
                <div className="nx-muted" style={{ fontSize: "var(--fs-12)" }}>{PROBLEM.has(vm.etat) ? <span className="nx-tone-warning">{t(`vmlist.reason.${vm.etat}`)}</span> : vm.os || "—"}</div>
                <TagChips tags={tagsOf(vm)} onTag={setTagFilter} />
                <dl className="nx-dl2" style={{ gridTemplateColumns: "5.3333rem minmax(0,1fr)", marginTop: "var(--space-2)" }}>
                  <dt>{t("ns.node")}</dt><dd className="nx-mono">{nodeName(vm.node)}</dd>
                  <dt>IP</dt><dd className="nx-mono">{vm.ip || "—"}</dd>
                  <dt>{t("vmlist.resources")}</dt><dd className="nx-mono">{vm.vcpu} · {formatSizeMb(vm.memoire_mo, lang)}</dd>
                </dl>
              </article>
            );
  const renderRow = (vm) => {
                      const sel = showDetail && selected?.nom === vm.nom;
                      return (
                        <tr key={`${vm.node}:${vm.nom}`} className={`nx-rowlink${sel ? " is-sel" : PROBLEM.has(vm.etat) ? " is-warn" : ""}${ctx.is("vm", vmKey(vm)) ? " is-ctx" : ""}`} aria-selected={sel || undefined} tabIndex={0}
                          onClick={() => select(vm)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(vm); } }} onContextMenu={ctx.open("vm", vm, vmKey(vm))}>
                          {selectable && <td className="nx-pickcell">{pickBox(vm)}</td>}
                          <td style={{ width: "2rem" }}><StatusIndicator kind="vm" wire={vm.etat} compact /></td>
                          <th scope="row" className="nx-nm"><button type="button" className="nx-lnk" onClick={(e) => { e.stopPropagation(); navigateTo("vm", vmKey(vm), "summary"); }}>{vm.nom}</button>{sub(vm)}</th>
                          {col("node") && <td>{nodeCell(vm.node)}</td>}
                          {col("ip") && <td className="nx-mono">{vm.ip || <span className="nx-muted">—</span>}</td>}
                          {col("os") && <td>{vm.os || <span className="nx-muted">—</span>}</td>}
                          {col("res") && <td className="nx-num nx-mono">{vm.vcpu} · {formatSizeMb(vm.memoire_mo, lang)}</td>}
                          {col("uptime") && <td className="nx-mono nx-muted">{vm.etat === "actif" ? formatUptimeLong(vm.uptime_s, lang) || "—" : "—"}</td>}
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
        {(tagList.length > 0 || tagFilter) && (
          <select className="nx-sel" aria-label={t("vmlist.filterTag")} value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}>
            <option value="">{t("vmlist.allTags")}</option>
            {tagList.map((tg) => <option key={tg} value={tg}>{tg} ({vms.filter((v) => tagsOf(v).includes(tg)).length})</option>)}
          </select>
        )}
        <select className="nx-sel" aria-label={t("vmlist.groupBy")} value={mode} onChange={(e) => setGroupMode(e.target.value)}>
          <option value="node">{t("vmlist.group.node")}</option>
          <option value="tag">{t("vmlist.group.tag")}</option>
          {caps.admin && <option value="pool">{t("vmlist.group.pool")}</option>}
          <option value="none">{t("vmlist.group.none")}</option>
        </select>
        <details className="nx-colpick">
          <summary className="nx-btn nx-btn--sm"><Columns3 size={14} aria-hidden="true" />{t("vmlist.columns")}</summary>
          <fieldset className="nx-colpick-p"><legend className="nx-sr">{t("vmlist.columns")}</legend>
            {OPTIONAL_COLUMNS.map((c) => (
              <label key={c} className="nx-check"><input type="checkbox" checked={columns.includes(c)} disabled={c === "node" && mode === "node"}
                onChange={(e) => setColumns(e.target.checked ? OPTIONAL_COLUMNS.filter((x) => x === c || columns.includes(x)) : columns.filter((x) => x !== c))} /> {t(`vmlist.col.${c}`)}</label>
            ))}
          </fieldset>
        </details>
        <select className="nx-sel" aria-label={t("vmlist.views")} value={currentView} onChange={(e) => applyView(e.target.value)}>
          <option value="">{t("vmlist.viewsNone")}</option>
          {savedViews.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
        </select>
        <button type="button" className="nx-btn nx-btn--sm" onClick={saveCurrentView}>{t("vmlist.saveView")}</button>
        {currentView && <button type="button" className="nx-btn nx-btn--sm nx-btn--ghost nx-btn--icon" aria-label={t("vmlist.deleteViewX", { name: currentView })} onClick={removeCurrentView}><X size={14} aria-hidden="true" /></button>}
        <label className="nx-search2"><Search size={15} aria-hidden="true" /><input type="search" aria-label={t("ns.filterVms")} placeholder={t("vmlist.searchPh")} value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <div className="nx-seg2" role="group" aria-label={t("ns.viewMode")}>
          <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>{t("ns.table")}</button>
          <button type="button" aria-pressed={view === "cards"} onClick={() => setView("cards")}>{t("ns.cards")}</button>
        </div>
      </div>

      {pickedVms.length > 0 && <BulkBar selected={pickedVms} caps={caps} nodes={nodes} onClear={clearPicks} />}
      {vms.length === 0 ? (
        <div className="nx-card2"><Empty icon={Monitor} title={t("ns.noVms")} text={t("vmlist.noneHelp")} action={caps.create && <button type="button" className="nx-btn" onClick={() => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" }))}><Plus size={15} aria-hidden="true" />{t("vmlist.create")}</button>} /></div>
      ) : view === "cards" ? (
        shown.length === 0 ? <p className="nx-muted" role="status">{t("act.noneFiltered")}</p> : (
          group ? (
            <div className="nx-stack">
              {groups.map((g) => (
                <section key={g.key} aria-label={g.label}>
                  <h2 className="nx-grouphead nx-grouphead--cards">{band(g, collapsed.has(g.key))}</h2>
                  {collapsed.has(g.key) ? null : g.vms.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("ns.noVms")}</p> : <div className="nx-cards2">{g.vms.map((vm) => renderCard(vm))}</div>}
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
                  <thead><tr>{selectable && (
                    <th scope="col" className="nx-pickcell">
                      <input type="checkbox" className="nx-pick" aria-label={t("bulk.pickAll", { n: shown.length })} checked={shown.length > 0 && shownPicked === shown.length}
                        ref={(el) => { if (el) el.indeterminate = shownPicked > 0 && shownPicked < shown.length; }} onChange={pickAllShown} />
                    </th>
                  )}{th("state", <span className="nx-sr">{t("ns.col.state")}</span>)}{th("name", t("ns.col.name"))}{col("node") && th("node", t("ns.node"))}{col("ip") && <th scope="col">{t("vmlist.ip")}</th>}{col("os") && <th scope="col">{t("vmlist.col.os")}</th>}{col("res") && th("res", t("vmlist.cpuMem"), "nx-num")}{col("uptime") && th("uptime", t("ns.col.uptime"))}</tr></thead>
                  {group ? groups.map((g) => {
                    const folded = collapsed.has(g.key);
                    const isNode = g.kind === "node" && g.node.id !== "?";
                    return (
                      <tbody key={g.key} className="nx-group">
                        <tr className={`nx-grouprow nx-grouprow--band${isNode && ctx.is("node", g.node.id) ? " is-ctx" : ""}`} onContextMenu={isNode ? ctx.open("node", g.node) : undefined}>
                          <th scope="colgroup" colSpan={colCount}>{band(g, folded)}</th>
                        </tr>
                        {folded ? null : g.vms.length === 0 ? <tr><td colSpan={colCount} className="nx-muted" style={{ paddingLeft: "2.6667rem" }}>{t("ns.noVms")}</td></tr> : g.vms.map((vm) => renderRow(vm))}
                      </tbody>
                    );
                  }) : <tbody>{shown.map((vm) => renderRow(vm))}</tbody>}
                  <tfoot><tr><td colSpan={colCount}>{t("vmlist.footer", { shown: shown.length, total: vms.length, vcpu, mem: formatSizeMb(mem, lang) })}</td></tr></tfoot>
                </table>
              </TableWrap>
            )}
          </div>
          {showDetail && <DetailPanel vm={selected} onClose={() => { setDetail(false); setSelName(null); }} />}
        </div>
      )}
      {ctx.target?.kind === "vm" && <VmContextMenu key={`vm:${ctx.target.obj.nom}`} vm={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
      {ctx.target?.kind === "node" && <NodeContextMenu key={`node:${ctx.target.obj.id}`} node={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
    </>
  );
}
VmList.ownHeader = true;
