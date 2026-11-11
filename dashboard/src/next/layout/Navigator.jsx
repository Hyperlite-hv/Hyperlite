import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Activity, Archive, Bell, Box, Boxes, Braces, Camera, ChevronDown, ChevronRight, ChevronsDownUp, Copy, Database, Disc3, Globe, KeyRound, ListFilter, Monitor, Network, ScrollText, Server, Share, Shield, Users, Workflow, Zap } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { useFreshness } from "../lib/inventory";
import { SECTIONS, sectionPages } from "../lib/sections";
import { findVm, vmKey } from "../lib/vmId";
import { nodeTone } from "../lib/nodeState";
import { fetchApiDocsAccess } from "../../api/client";
import { useContextTarget } from "../components/ContextMenu";
import { NodeContextMenu, VmContextMenu } from "../components/ObjectActions";

const PAGE_ICONS = {
  storage: Database, network: Network, firewall: Shield, backups: Archive, snapshots: Camera, exports: Share, sites: Globe,
  activity: Activity, journal: ScrollText, metrics: Activity, notifications: Bell, automation: Zap, api: Braces,
  permissions: Users, sso: KeyRound, cfs: Copy,
};
const PAGE_LABELS = { activity: "nav.activity", permissions: "nav.usersRoles", sso: "tab.sso", journal: "tab.journal" };

const OPEN_KEY = "hyperlite-next-tree-open";
function readOpen() { try { return new Set(JSON.parse(localStorage.getItem(OPEN_KEY) || "null") || ["root"]); } catch { return new Set(["root"]); } }

function vmTone(v) {
  if (v.state === "active") return { tone: "success", key: "state.running" };
  if (v.state === "crashed" || v.state === "blocked") return { tone: "danger", key: "tree.state.problem" };
  if (v.state === "paused") return { tone: "warning", key: "tree.state.paused" };
  return { tone: "offline", key: "tree.state.stopped", square: true };
}

const KIND_ICONS = { root: Workflow, node: Server, vm: Monitor, containers: Box, ct: Box, kubernetes: Boxes, library: Disc3 };

// The inventory as a tree: the cluster, its nodes and their VMs, then the containers and the library.
// Arrow keys move and open (WAI-ARIA tree pattern); a right click opens the object's actions.
function InventoryTree({ filter }) {
  const t = useT();
  const { nodes, vms, selection, tab, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, selection: s.selection, tab: s.activeTab, navigateTo: s.navigateTo })));
  const { cluster, containers } = useFreshness(useShallow((s) => ({ cluster: s.cluster, containers: s.containers })));
  const [open, setOpen] = useState(readOpen);
  const [focus, setFocus] = useState(0);
  const listRef = useRef(null);
  const ctx = useContextTarget();
  const q = filter.trim().toLowerCase();

  const toggle = (key, force) => setOpen((prev) => {
    const next = new Set(prev);
    const want = force ?? !next.has(key);
    if (want) next.add(key); else next.delete(key);
    try { localStorage.setItem(OPEN_KEY, JSON.stringify([...next])); } catch { /* preference only */ }
    return next;
  });
  useEffect(() => {
    const collapse = () => { setOpen(new Set(["root"])); try { localStorage.setItem(OPEN_KEY, JSON.stringify(["root"])); } catch { /* preference only */ } };
    window.addEventListener("nx:tree-collapse", collapse);
    return () => window.removeEventListener("nx:tree-collapse", collapse);
  }, []);

  const go = (type, id, page = "summary") => { navigateTo(type, id, page); window.dispatchEvent(new Event("nx:navigated")); };
  // A bare name in the URL means the only VM with that name: compare canonical keys.
  const selectedVm = selection.type === "vm" ? vmKey(findVm(vms, selection.id)) || selection.id : null;
  const dcPage = selection.type === "datacenter" ? tab || "summary" : null;

  const rows = useMemo(() => {
    const out = [];
    const inCluster = Boolean(cluster?.in_cluster);
    const vmMatch = (v) => !q || [v.name, v.ip, v.os, v.vmid != null ? String(v.vmid) : null].some((x) => x && String(x).toLowerCase().includes(q));
    const rootOpen = q || open.has("root");
    out.push({ key: "root", depth: 0, kind: "root", label: inCluster ? cluster.name : t("crumb.datacenter"), meta: inCluster ? t("tree.nodes", { n: nodes.filter((n) => n.id === "local" || n.member).length }) : null,
      expandable: true, expanded: rootOpen, selected: dcPage && ["summary", "nodes", "vms", "perf", "ha", "drs", "compat"].includes(dcPage), onSelect: () => go("datacenter", null, "summary") });
    if (!rootOpen) return out;
    const byName = [...nodes].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    const members = byName.filter((n) => !inCluster || n.id === "local" || n.member);
    const outside = inCluster ? byName.filter((n) => n.id !== "local" && !n.member) : [];
    const addNode = (n, depth, extraMeta) => {
      const mine = vms.filter((v) => (v.node || "local") === n.id).sort((a, b) => (a.vmid ?? 1e9) - (b.vmid ?? 1e9) || a.name.localeCompare(b.name));
      const shown = q && !n.name.toLowerCase().includes(q) ? mine.filter(vmMatch) : mine;
      if (q && shown.length === 0 && !n.name.toLowerCase().includes(q)) return;
      const key = `node:${n.id}`;
      const holdsSelected = selectedVm && mine.some((v) => vmKey(v) === selectedVm);
      const expanded = Boolean(q) || open.has(key) || holdsSelected;
      const s = nodeTone(n);
      out.push({ key, depth, kind: "node", label: n.name, meta: extraMeta || (n.maintenance ? t("tree.maint") : String(mine.length)), tone: s.tone, stateKey: s.key,
        expandable: mine.length > 0, expanded, selected: selection.type === "node" && selection.id === n.id, obj: n, onSelect: () => go("node", n.id) });
      if (!expanded) return;
      for (const v of shown) {
        const vs = vmTone(v);
        out.push({ key: `vm:${vmKey(v)}`, depth: depth + 1, kind: "vm", label: v.name, meta: v.vmid != null ? String(v.vmid) : null, metaMono: true, tone: vs.tone, square: vs.square, stateKey: vs.key,
          selected: selectedVm === vmKey(v), obj: v, onSelect: () => go("vm", vmKey(v)) });
      }
    };
    members.forEach((n) => addNode(n, 1));
    outside.forEach((n) => addNode(n, 1, t("tree.outside")));
    if (!q || "containers".includes(q) || containers.some((c) => c.name.toLowerCase().includes(q))) {
      const cts = q ? containers.filter((c) => c.name.toLowerCase().includes(q)) : containers;
      const expanded = Boolean(q) || open.has("containers") || selection.type === "container";
      out.push({ key: "containers", depth: 0, kind: "containers", label: t("nav.containers"), meta: String(containers.length), expandable: cts.length > 0, expanded,
        selected: dcPage === "containers", onSelect: () => go("datacenter", null, "containers") });
      if (expanded) for (const c of cts) out.push({ key: `ct:${c.name}`, depth: 1, kind: "ct", label: c.name, tone: c.state === "active" ? "success" : "offline", square: c.state !== "active",
        stateKey: c.state === "active" ? "state.running" : "tree.state.stopped", selected: selection.type === "container" && selection.id === c.name, onSelect: () => go("container", c.name) });
    }
    if (!q) {
      out.push({ key: "kubernetes", depth: 0, kind: "kubernetes", label: t("nav.kubernetes"), selected: dcPage === "kubernetes", onSelect: () => go("datacenter", null, "kubernetes") });
      out.push({ key: "library", depth: 0, kind: "library", label: t("nav.library"), selected: dcPage === "library" || dcPage === "templates", onSelect: () => go("datacenter", null, "library") });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, vms, containers, cluster, open, q, selection.type, selection.id, dcPage, t]);

  // Keep the roving focus on the selected row when the selection changes from elsewhere.
  const selIndex = rows.findIndex((r) => r.selected);
  useEffect(() => { if (selIndex >= 0) setFocus(selIndex); }, [selIndex]);
  const f = Math.min(focus, rows.length - 1);

  const parentIndex = (i) => { for (let j = i - 1; j >= 0; j--) if (rows[j].depth < rows[i].depth) return j; return -1; };
  const move = (i) => { const n = Math.max(0, Math.min(rows.length - 1, i)); setFocus(n); requestAnimationFrame(() => listRef.current?.querySelectorAll("[role=treeitem]")[n]?.focus()); };
  function onKeyDown(e) {
    const r = rows[f];
    if (!r) return;
    if (e.key === "ArrowDown") { e.preventDefault(); move(f + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(f - 1); }
    else if (e.key === "Home") { e.preventDefault(); move(0); }
    else if (e.key === "End") { e.preventDefault(); move(rows.length - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); if (r.expandable && !r.expanded) toggle(r.key, true); else if (r.expanded) move(f + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); if (r.expanded && r.expandable) toggle(r.key, false); else { const p = parentIndex(f); if (p >= 0) move(p); } }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); r.onSelect(); }
  }

  if (q && rows.length <= 1) return <p className="nx-tree-empty" role="status">{t("tree.noMatch")}</p>;
  return (
    <>
      <ul ref={listRef} className="nx-tree" role="tree" aria-label={t("tree.label")} onKeyDown={onKeyDown}>
        {rows.map((r, i) => {
          const I = KIND_ICONS[r.kind];
          return (
            <li key={r.key} role="treeitem" aria-level={r.depth + 1} aria-selected={r.selected ? "true" : "false"} aria-expanded={r.expandable ? r.expanded : undefined}
              tabIndex={i === f ? 0 : -1} className={`nx-tree-row${r.selected ? " is-sel" : ""}${ctx.is(r.kind, r.key) ? " is-ctx" : ""}`} style={{ "--depth": r.depth }}
              onClick={() => { setFocus(i); r.onSelect(); }} onFocus={() => setFocus(i)}
              onContextMenu={r.kind === "vm" || r.kind === "node" ? ctx.open(r.kind, r.obj, r.key) : undefined}>
              <span className="nx-tree-chev" aria-hidden="true" onClick={r.expandable ? (e) => { e.stopPropagation(); toggle(r.key); } : undefined}>
                {r.expandable ? (r.expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : null}
              </span>
              {I && <I size={15} strokeWidth={1.75} className="nx-tree-ico" aria-hidden="true" />}
              <span className="nx-tree-lbl" title={r.label}>{r.label}</span>
              {r.meta && <span className={`nx-tree-meta${r.metaMono ? " nx-mono" : ""}`}>{r.meta}</span>}
              {r.tone && <span className={`nx-tree-dot${r.square ? " is-square" : ""}`} data-tone={r.tone} role="img" aria-label={t(r.stateKey)} />}
            </li>
          );
        })}
      </ul>
      {ctx.target?.kind === "vm" && <VmContextMenu key={ctx.target.key} vm={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
      {ctx.target?.kind === "node" && <NodeContextMenu key={ctx.target.key} node={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
    </>
  );
}

// The pages of a section other than the inventory, as a short list.
function SectionList({ section }) {
  const t = useT();
  const { selection, tab, navigateTo } = useInfraStore(useShallow((s) => ({ selection: s.selection, tab: s.activeTab, navigateTo: s.navigateTo })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const [apiDocs, setApiDocs] = useState(false);
  useEffect(() => {
    if (caps.admin || section.id !== "automation") return undefined;
    const check = () => fetchApiDocsAccess().then((r) => setApiDocs(Boolean(r?.allowed))).catch(() => setApiDocs(false));
    check();
    window.addEventListener("nx:api-docs-access", check);
    return () => window.removeEventListener("nx:api-docs-access", check);
  }, [caps.admin, section.id]);
  const pages = sectionPages(section, { admin: caps.admin, apiDocs });
  return (
    <div className="nx-secnav">
      {pages.map((id) => {
        const I = PAGE_ICONS[id];
        const active = selection.type === "datacenter" && (tab || "summary") === id;
        return (
          <button key={id} type="button" className={`nx-nav-item${active ? " active" : ""}`} aria-current={active ? "page" : undefined}
            onClick={() => { navigateTo("datacenter", null, id); window.dispatchEvent(new Event("nx:navigated")); }}>
            {I && <I size={17} aria-hidden="true" />}<span className="nx-nav-label">{t(PAGE_LABELS[id] || `nav.${id}`)}</span>
          </button>
        );
      })}
    </div>
  );
}

// The second column: the inventory tree, or the pages of the section chosen on the rail.
export default function Navigator({ section }) {
  const t = useT();
  const [filter, setFilter] = useState("");
  const sec = SECTIONS.find((s) => s.id === section) || SECTIONS[0];
  const inventory = sec.id === "inventory";
  return (
    <nav className="nx-navi" aria-label={t("nav.main")}>
      <div className="nx-navi-head">
        <h2>{t(sec.label)}</h2>
        {inventory && (
          <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("tree.collapse")} title={t("tree.collapse")} onClick={() => window.dispatchEvent(new Event("nx:tree-collapse"))}>
            <ChevronsDownUp size={15} aria-hidden="true" />
          </button>
        )}
      </div>
      {inventory ? (
        <>
          <label className="nx-navi-filter">
            <ListFilter size={14} aria-hidden="true" />
            <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("tree.filter")} aria-label={t("tree.filter")} />
          </label>
          <div className="nx-navi-scroll"><InventoryTree filter={filter} /></div>
        </>
      ) : <div className="nx-navi-scroll"><SectionList section={sec} /></div>}
    </nav>
  );
}
