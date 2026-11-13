import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ChevronDown, ChevronRight, Download, Ellipsis, Group, List, Monitor, Plus, RefreshCw, Search, SlidersHorizontal, Tag, Users, X } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { vmKey } from "../lib/vmId";
import { capabilities } from "../lib/capabilities";
import { formatSizeMb, formatUptimeLong } from "../lib/format";
import { refreshInventory } from "../lib/inventory";
import { stateInfo } from "../lib/enums";
import StatusIndicator from "../components/StatusIndicator";
import { useContextTarget } from "../components/ContextMenu";
import { VmContextMenu, NodeContextMenu } from "../components/ObjectActions";
import { PageHeader, Empty, StatePill } from "../components/ui";
import { Cmd, CmdSep, FilterPill, PickCmd, downloadCsv } from "../components/portal";
import BulkBar from "../components/BulkBar";
import { TagChips } from "../components/NotesCard";
import { allTags, metaOf, useMetaStore } from "../lib/meta";
import { OPTIONAL_COLUMNS, deleteView, groupVms, readColumns, readGroupMode, readSavedViews, saveColumns, saveGroupMode, saveView } from "../lib/vmViews";
import { fetchPools } from "../../api/client";
import { promptText } from "../../store/usePromptStore";
import { confirmAction } from "../../store/useConfirmStore";

const VIEW_KEY = "hyperlite-next-vmview";
const COLLAPSED_KEY = "hyperlite-next-vmcollapsed";
const PROBLEM = new Set(["crashed", "blocked", "unknown"]);
// Cards fit a phone better than a wide table: they are the default there, until the person chooses.
function readView() {
  try { const v = localStorage.getItem(VIEW_KEY); if (v === "cards" || v === "table") return v; } catch { /* preference only */ }
  return typeof window !== "undefined" && window.matchMedia?.("(max-width: 640px)").matches ? "cards" : "table";
}
function readCollapsed() { try { const v = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]"); return new Set(Array.isArray(v) ? v : []); } catch { return new Set(); } }

// Head of a group of the list: a fold toggle, the node (or tag, or pool) and how many machines it holds.
function GroupHead({ group, collapsed, onToggle }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const isNode = group.kind === "node" && group.node.id !== "?";
  const Icon = group.kind === "tag" ? Tag : group.kind === "pool" ? Users : null;
  return (
    <span className="hc-grp">
      <button type="button" className="hc-ibtn" aria-expanded={!collapsed}
        aria-label={t(collapsed ? "vmlist.expandGroup" : "vmlist.collapseGroup", { name: group.label })} onClick={onToggle}>
        {collapsed ? <ChevronRight size={16} aria-hidden="true" /> : <ChevronDown size={16} aria-hidden="true" />}
      </button>
      {Icon && <Icon size={14} aria-hidden="true" className="nx-muted" />}
      {isNode ? <button type="button" className="nx-lnk" onClick={() => navigateTo("node", group.node.id, "summary")}>{group.label}</button>
        : <span className={group.none ? "nx-muted" : undefined}>{group.label}</span>}
      <span className="nx-muted">({group.vms.length})</span>
      {isNode && group.node.state && group.node.state !== "online" && <StatePill kind="node" wire={group.node.state} />}
    </span>
  );
}

// "Virtual machines": every VM of every node in one list, grouped by node by default, with the commands of the
// page above it (the ones that act on a selection wait for one) and its filters read as sentences.
export default function VmList() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { vms, nodes, navigateTo } = useInfraStore(useShallow((s) => ({ vms: s.vms, nodes: s.nodes, navigateTo: s.navigateTo })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const [view, setViewState] = useState(readView);
  const [q, setQ] = useState("");
  const [chip, setChip] = useState("all");
  const [sort, setSort] = useState({ key: "state", dir: 1 });
  const [colAlign, setColAlign] = useState("start");
  const [nodeFilter, setNodeFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const { byKey: metaByKey, load: loadMeta } = useMetaStore(useShallow((s) => ({ byKey: s.byKey, load: s.load })));
  useEffect(() => { loadMeta(); }, [loadMeta]);
  const tagsOf = (v) => metaOf(metaByKey, "vm", v.node, v.name)?.tags || [];
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
  // Grouped by node, the node is named once in each group's head instead of on every row.
  const col = (c) => columns.includes(c) && !(c === "node" && mode === "node");
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleGroup = (id) => setCollapsed((prev) => {
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
  const nodeName = (id) => nodes.find((n) => n.id === id)?.name || id;
  const ctx = useContextTarget(); // right click (or the "..." of a row) on a VM, right click on a node: its actions
  // Selection for the commands that act on several VMs, by VM identity (node and name).
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
    running: vms.filter((v) => v.state === "active").length,
    stopped: vms.filter((v) => v.state === "stopped" || v.state === "shutting_down").length,
    problems: vms.filter((v) => PROBLEM.has(v.state)).length,
  }), [vms]);

  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    const list = vms.filter((v) => (!nodeFilter || v.node === nodeFilter) && (!tagFilter || tagsOf(v).includes(tagFilter)) && (chip === "all" || (chip === "running" && v.state === "active") || (chip === "stopped" && (v.state === "stopped" || v.state === "shutting_down")) || (chip === "problems" && PROBLEM.has(v.state)))
      && (!n || `${v.vmid ?? ""} ${v.name} ${v.ip || ""} ${v.os || ""} ${nodeName(v.node)} ${tagsOf(v).join(" ")}`.toLowerCase().includes(n)));
    const rank = (v) => (PROBLEM.has(v.state) ? 0 : v.state === "active" ? 1 : 2); // problems first
    const val = { state: rank, name: (v) => v.name.toLowerCase(), node: (v) => nodeName(v.node).toLowerCase(), os: (v) => (v.os || "").toLowerCase(), res: (v) => (v.vcpu || 0) * 1e6 + (v.memory_mb || 0), ip: (v) => v.ip || "", uptime: (v) => v.uptime_s || 0 }[sort.key] || rank;
    return [...list].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : a.name.localeCompare(b.name)) * sort.dir);
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
    <input type="checkbox" className="nx-pick" checked={picked.has(vmKey(vm))} aria-label={t("bulk.pick", { name: vm.name })}
      onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} onChange={() => togglePick(vm)} />
  );

  // One group per node, in the order of the nodes page; a node without VMs is shown too when nothing is filtered.
  const filtering = chip !== "all" || q.trim() !== "" || tagFilter !== "";
  const groups = groupVms(shown, mode, {
    nodes: nodes.filter((n) => !nodeFilter || n.id === nodeFilter), tagsOf, pools, withEmpty: !filtering,
    noneLabel: t(mode === "node" ? "vmlist.unknownNode" : mode === "tag" ? "vmlist.noTag" : "vmlist.noPool"),
  }) || [];
  const COLS = ["node", "os", "res", "ip", "uptime"].filter(col);
  // The tags column only when some machine has a tag: an empty column is noise.
  const anyTags = vms.some((v) => tagsOf(v).length > 0);
  const colCount = 3 + (anyTags ? 1 : 0) + COLS.length + (selectable ? 1 : 0);
  const th = (key, label) => (
    <th scope="col" aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="nx-thbtn" onClick={() => setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }))}>{label}<span className="hc-sort" aria-hidden="true">{sort.key === key ? (sort.dir === 1 ? "↑" : "↓") : "↑↓"}</span></button>
    </th>
  );
  const size = (v) => t("vmlist.size", { vcpu: v.vcpu, mem: formatSizeMb(v.memory_mb, lang) });
  const nodeCell = (id) => {
    const n = nodes.find((x) => x.id === id);
    return n ? <button type="button" className="nx-lnk" title={n.name} onClick={(e) => { e.stopPropagation(); navigateTo("node", n.id, "summary"); }}>{n.name}</button> : <span>{id}</span>;
  };
  const open = (vm) => navigateTo("vm", vmKey(vm), "summary");
  const exportCsv = () => downloadCsv("virtual-machines.csv", [
    [t("ns.col.name"), t("ns.node"), t("ns.col.state"), t("vmlist.col.os"), "vCPU", t("ns.memory"), t("vmlist.ip"), t("vmlist.tags")],
    ...shown.map((v) => [v.name, nodeName(v.node), t(stateInfo("vm", v.state).key), v.os || "", v.vcpu, formatSizeMb(v.memory_mb, lang), v.ip || "", tagsOf(v).join(", ")]),
  ]);

  const renderCard = (vm) => (
    <article key={`${vm.node}:${vm.name}`} className={`nx-card2 nx-vmcard2${ctx.is("vm", vmKey(vm)) ? " is-ctx" : ""}`} aria-label={vm.name} onContextMenu={ctx.open("vm", vm, vmKey(vm))}>
      <div className="nx-inline">{selectable && pickBox(vm)}<Monitor size={16} aria-hidden="true" className="nx-muted" /><button type="button" className="nx-lnk" onClick={() => open(vm)}>{vm.name}</button></div>
      <StatusIndicator kind="vm" wire={vm.state} />
      <dl className="nx-dl2" style={{ gridTemplateColumns: "6.6667rem minmax(0,1fr)", marginTop: "var(--space-2)" }}>
        <dt>{t("ns.node")}</dt><dd>{nodeName(vm.node)}</dd>
        <dt>{t("vmlist.col.os")}</dt><dd>{vm.os || "—"}</dd>
        <dt>{t("vmlist.col.res")}</dt><dd>{size(vm)}</dd>
        <dt>{t("vmlist.ip")}</dt><dd>{vm.ip || "—"}</dd>
      </dl>
      <TagChips tags={tagsOf(vm)} onTag={setTagFilter} />
    </article>
  );
  const renderRow = (vm) => (
    <tr key={`${vm.node}:${vm.name}`} className={`${picked.has(vmKey(vm)) ? "is-sel" : ""}${ctx.is("vm", vmKey(vm)) ? " is-ctx" : ""}`} onContextMenu={ctx.open("vm", vm, vmKey(vm))}>
      {selectable && <td className="nx-pickcell">{pickBox(vm)}</td>}
      <th scope="row" className="hc-namecell">
        <div><Monitor size={16} aria-hidden="true" />
          <span><button type="button" className="nx-lnk" title={vm.name} onClick={() => open(vm)}>{vm.name}</button>
            {PROBLEM.has(vm.state) && <small className="is-warn">{t(`vmlist.reason.${vm.state}`)}</small>}</span></div>
      </th>
      {col("node") && <td>{nodeCell(vm.node)}</td>}
      <td><StatusIndicator kind="vm" wire={vm.state} /></td>
      {col("os") && <td className="hc-cut" title={vm.os || undefined}>{vm.os || <span className="nx-muted">—</span>}</td>}
      {col("res") && <td>{size(vm)}</td>}
      {col("ip") && <td className="hc-num">{vm.ip || <span className="nx-muted">—</span>}</td>}
      {col("uptime") && <td className="nx-muted">{vm.state === "active" ? formatUptimeLong(vm.uptime_s, lang) || "—" : "—"}</td>}
      {anyTags && <td className="hc-tagcell"><TagChips tags={tagsOf(vm)} onTag={setTagFilter} /></td>}
      <td className="hc-rowact"><button type="button" className="hc-ibtn" aria-label={t("ctx.menuOf", { name: vm.name })} onClick={ctx.open("vm", vm, vmKey(vm))}><Ellipsis size={16} aria-hidden="true" /></button></td>
    </tr>
  );

  const manageView = (
    <details className="nx-colpick" onToggle={(e) => {
      if (!e.currentTarget.open) return;
      const r = e.currentTarget.getBoundingClientRect();
      setColAlign(r.left + e.currentTarget.querySelector(".nx-colpick-p").offsetWidth > window.innerWidth - 8 ? "end" : "start");
    }}>
      <summary className="nx-btn"><SlidersHorizontal size={16} aria-hidden="true" />{t("list.manageView")}<ChevronDown size={14} aria-hidden="true" className="hc-cmd-chev" /></summary>
      <div className="nx-colpick-p hc-viewopts" data-align={colAlign}>
        <fieldset className="hc-viewopts-f"><legend>{t("vmlist.columns")}</legend>
          {OPTIONAL_COLUMNS.map((c) => (
            <label key={c} className="nx-check"><input type="checkbox" checked={columns.includes(c)} disabled={c === "node" && mode === "node"}
              title={c === "node" && mode === "node" ? t("vmlist.nodeInGroups") : undefined}
              onChange={(e) => setColumns(e.target.checked ? OPTIONAL_COLUMNS.filter((x) => x === c || columns.includes(x)) : columns.filter((x) => x !== c))} /> {t(`vmlist.col.${c}`)}</label>
          ))}
        </fieldset>
        <div className="hc-viewopts-f"><span>{t("vmlist.views")}</span>
          <select className="nx-sel" aria-label={t("vmlist.views")} value={currentView} onChange={(e) => applyView(e.target.value)}>
            <option value="">{t("vmlist.viewsNone")}</option>
            {savedViews.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
          </select>
          <div className="nx-inline">
            <button type="button" className="nx-btn nx-btn--sm" onClick={saveCurrentView}>{t("vmlist.saveView")}</button>
            {currentView && <button type="button" className="nx-btn nx-btn--sm nx-btn--ghost nx-btn--icon" aria-label={t("vmlist.deleteViewX", { name: currentView })} onClick={removeCurrentView}><X size={14} aria-hidden="true" /></button>}
          </div>
        </div>
      </div>
    </details>
  );

  return (
    <>
      <PageHeader title={t("tab.vms")} actions={(
        <>
          {caps.create && <Cmd icon={Plus} label={t("action.create")} onClick={() => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" }))} />}
          {manageView}
          <Cmd icon={RefreshCw} label={t("action.refresh")} onClick={() => refreshInventory().catch(() => {})} />
          <Cmd icon={Download} label={t("list.exportCsv")} onClick={exportCsv} disabled={shown.length === 0} />
          {selectable && <><CmdSep /><BulkBar bare selected={pickedVms} caps={caps} nodes={nodes} onClear={clearPicks} /></>}
        </>
      )} />

      <div className="hc-fbar">
        <label className="hc-filter hc-filter--wide"><Search size={16} aria-hidden="true" /><input type="search" aria-label={t("ns.filterVms")} placeholder={t("list.filterAny")} value={q} onChange={(e) => setQ(e.target.value)} /></label>
        {nodes.length > 1 && (
          <FilterPill label={t("list.nodeIs")} value={nodeFilter} onChange={setNodeFilter}
            options={[{ value: "", label: t("list.all") }, ...nodes.map((n) => ({ value: n.id, label: `${n.name} (${vms.filter((v) => v.node === n.id).length})` }))]} />
        )}
        <FilterPill label={t("list.stateIs")} value={chip === "all" ? "" : chip} onChange={(v) => setChip(v || "all")}
          options={[{ value: "", label: t("list.all") }, { value: "running", label: `${t("state.running")} (${counts.running})` }, { value: "stopped", label: `${t("state.stopped")} (${counts.stopped})` }, { value: "problems", label: `${t("vmlist.problems")} (${counts.problems})` }]} />
        {(tagList.length > 0 || tagFilter) && (
          <FilterPill label={t("list.tagIs")} value={tagFilter} onChange={setTagFilter}
            options={[{ value: "", label: t("list.all") }, ...tagList.map((tg) => ({ value: tg, label: tg }))]} />
        )}
        <span className="nx-sp" />
        <PickCmd icon={Group} label={t("vmlist.groupBy")} value={mode} onChange={setGroupMode} options={[
          { value: "none", label: t("list.noGrouping") }, { value: "node", label: t("list.groupByNode") }, { value: "tag", label: t("list.groupByTag") },
          ...(caps.admin ? [{ value: "pool", label: t("list.groupByPool") }] : []),
        ]} />
        <PickCmd icon={List} label={t("ns.viewMode")} value={view} onChange={setView} options={[{ value: "table", label: t("list.viewList") }, { value: "cards", label: t("list.viewCards") }]} />
      </div>
      <p className="hc-count" role="status">{t("list.showing", { n: shown.length, total: vms.length })}{pickedVms.length > 0 && <> · {t("bulk.selected", { n: pickedVms.length })} <button type="button" className="nx-lnk" onClick={clearPicks}>{t("bulk.clear")}</button></>}</p>

      {vms.length === 0 ? (
        <Empty icon={Monitor} title={t("ns.noVms")} text={t("vmlist.noneHelp")} action={caps.create && <button type="button" className="nx-btn nx-btn--primary" onClick={() => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" }))}><Plus size={15} aria-hidden="true" />{t("vmlist.create")}</button>} />
      ) : shown.length === 0 ? <p className="nx-muted" role="status">{t("act.noneFiltered")}</p>
      : view === "cards" ? (
        group ? (
          <div className="nx-stack">
            {groups.map((g) => (
              <section key={g.key} aria-label={g.label}>
                <h2 className="hc-grp-h"><GroupHead group={g} collapsed={collapsed.has(g.key)} onToggle={() => toggleGroup(g.key)} /></h2>
                {collapsed.has(g.key) ? null : g.vms.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("ns.noVms")}</p> : <div className="nx-cards2">{g.vms.map(renderCard)}</div>}
              </section>
            ))}
          </div>
        ) : <div className="nx-cards2">{shown.map(renderCard)}</div>
      ) : (
        <div className="hc-gridwrap">
          <table className="hc-grid hc-grid--list">
            <thead><tr>
              {selectable && (
                <th scope="col" className="nx-pickcell">
                  <input type="checkbox" className="nx-pick" aria-label={t("bulk.pickAll", { n: shown.length })} checked={shown.length > 0 && shownPicked === shown.length}
                    ref={(el) => { if (el) el.indeterminate = shownPicked > 0 && shownPicked < shown.length; }} onChange={pickAllShown} />
                </th>
              )}
              {th("name", t("ns.col.name"))}
              {col("node") && th("node", t("ns.node"))}
              {th("state", t("ns.col.state"))}
              {col("os") && th("os", t("vmlist.col.os"))}
              {col("res") && th("res", t("vmlist.col.res"))}
              {col("ip") && th("ip", t("vmlist.ip"))}
              {col("uptime") && th("uptime", t("vmlist.col.uptime"))}
              {anyTags && <th scope="col">{t("vmlist.tags")}</th>}
              <th scope="col"><span className="nx-sr">{t("actions")}</span></th>
            </tr></thead>
            {group ? groups.map((g) => {
              const folded = collapsed.has(g.key);
              const isNode = g.kind === "node" && g.node.id !== "?";
              return (
                <tbody key={g.key}>
                  <tr className={`hc-grprow${isNode && ctx.is("node", g.node.id) ? " is-ctx" : ""}`} onContextMenu={isNode ? ctx.open("node", g.node) : undefined}>
                    <th scope="colgroup" colSpan={colCount}><GroupHead group={g} collapsed={folded} onToggle={() => toggleGroup(g.key)} /></th>
                  </tr>
                  {folded ? null : g.vms.length === 0 ? <tr><td colSpan={colCount} className="nx-muted hc-emptyrow">{t("ns.noVms")}</td></tr> : g.vms.map(renderRow)}
                </tbody>
              );
            }) : <tbody>{shown.map(renderRow)}</tbody>}
          </table>
        </div>
      )}
      {ctx.target?.kind === "vm" && <VmContextMenu key={`vm:${ctx.target.obj.name}`} vm={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
      {ctx.target?.kind === "node" && <NodeContextMenu key={`node:${ctx.target.obj.id}`} node={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
    </>
  );
}
VmList.ownHeader = true;
