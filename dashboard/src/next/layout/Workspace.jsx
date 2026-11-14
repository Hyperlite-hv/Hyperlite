import { Suspense, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { findVm, isRemoteVm } from "../lib/vmId";
import { EmptyState } from "../components/States";
import PermissionNotice from "../components/PermissionNotice";
import { CONTAINER_TABS, DATACENTER_TABS, NODE_TABS, OWN_HEADER, VM_TABS, locate } from "../legacy/tabs";
import { selectionToPath, withTab } from "../lib/urls";
import { capabilities } from "../lib/capabilities";
import { useFreshness } from "../lib/inventory";
import { formatUptimeLong, formatVersionInt, normalize } from "../lib/format";
import { ChevronRight, Search, X } from "lucide-react";
import { LIST_OF, RESOURCE_MENUS, resourcePageLabel, serviceLabel, useConsoleStore } from "../lib/services";
import ServiceTile from "../components/ServiceTile";
import { setDocumentTitle } from "../lib/environment";
import { Loading, PageHeader, StatePill } from "../components/ui";
import { VmHeaderActions, NodeHeaderActions } from "../components/ObjectActions";
import { ContainerHeaderActions } from "../pages/ContainerPages";
import { formatSizeMb } from "../lib/format";
import PendingChanges from "../components/PendingChanges";
import DatabaseBanner from "../components/DatabaseBanner";



// Keeps the URL and the store selection in step, in both directions. Unlike the legacy hook it
// PUSHES history entries, so the browser Back button restores the previous selection and tab.
function useUrlSync() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { selection, pendingTab, activeTab } = useInfraStore(useShallow((s) => ({ selection: s.selection, pendingTab: s.pendingTab, activeTab: s.activeTab })));
  const select = useInfraStore((s) => s.select);
  const setActiveTab = useInfraStore((s) => s.setActiveTab);
  const clearPendingTab = useInfraStore((s) => s.clearPendingTab);

  // URL -> store
  useEffect(() => {
    const m = pathname.match(/^\/(node|vm|container)\/([^/]+)/);
    const cur = useInfraStore.getState().selection;
    if (m) {
      const id = decodeURIComponent(m[2]);
      if (cur.type !== m[1] || cur.id !== id) select(m[1], id);
    } else if (pathname.startsWith("/datacenter") && cur.type !== "datacenter" && cur.type !== "storage" && cur.type !== "container") {
      select("datacenter", null);
    }
    const tab = new URLSearchParams(search).get("tab") || "summary";
    if (useInfraStore.getState().activeTab !== tab) setActiveTab(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, search]);

  // store -> URL (selection or requested tab changed from the sidebar, a link, the palette...). Clearing the
  // requested tab re-runs this effect before the location has caught up with the navigation just made: that run
  // must not act, otherwise it sends a page reached from a VM (say ?tab=vms) back to the summary.
  const handled = useRef(null);
  useEffect(() => {
    const sel = useInfraStore.getState().selection;
    const base = selectionToPath(sel);
    if (!base) return;
    const key = `${sel.type}:${sel.id ?? ""}`;
    if (!pendingTab && handled.current === key) return;
    handled.current = key;
    const params = new URLSearchParams(search);
    const currentTab = params.get("tab") || "summary";
    // The same page whether its address spells "@" or "%40" (a remote VM's key, typed or pasted by hand): the tab of
    // the address is kept instead of falling back to the summary.
    const samePath = (x, y) => { try { return decodeURIComponent(x) === decodeURIComponent(y); } catch { return x === y; } };
    const wantedTab = pendingTab || (samePath(base, pathname) ? currentTab : "summary");
    const target = withTab(base, wantedTab);
    if (pendingTab) clearPendingTab();
    if (!samePath(target, `${pathname}${search}`)) navigate(target);
    if (useInfraStore.getState().activeTab !== wantedTab) setActiveTab(wantedTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.type, selection.id, pendingTab]);
  return activeTab;
}

// VM pages whose endpoints only manage the VMs of this host (they take no node): for a remote VM they would read
// and change a local VM of the same name, so they are replaced by an explanation.
// "options" is not listed: start at boot works on every node; its local-only sections say so themselves.
const LOCAL_ONLY_VM_PAGES = new Set(["hardware", "network", "backup", "snapshots", "cloudinit", "advanced"]);

// Datacenter pages the backend reserves to administrators (their endpoints answer 403 to anyone else).
const ADMIN_ONLY = new Set(["permissions", "sso", "journal", "exports", "metrics", "cfs", "sites"]);

// The line of facts under the commands of a resource: state first, then what identifies it.
function VmFacts({ vm, node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  return (
    <div className="nx-ometa hc-facts">
      <StatePill kind="vm" wire={vm.state} />
      {vm.os && <span>{vm.os}</span>}
      <span className="nx-sep" aria-hidden="true">·</span>
      <button type="button" className="nx-lnk" onClick={() => navigateTo("node", vm.node, "summary")}>{node?.name || vm.node}</button>
      <span className="nx-sep" aria-hidden="true">·</span>
      <span className="nx-mono" title={vm.ip ? undefined : t("ns.ipHelp")}>{vm.ip || t("vm.ipNone")}</span>
      {vm.state === "active" && vm.uptime_s ? <><span className="nx-sep" aria-hidden="true">·</span><span>{t("vm.since", { d: formatUptimeLong(vm.uptime_s, lang) })}</span></> : null}
    </div>
  );
}
function ContainerFacts({ ct }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  return (
    <div className="nx-ometa hc-facts">
      <StatePill kind="vm" wire={ct.state === "active" ? "active" : "stopped"} />
      <span>{ct.mode === "application" ? `Docker · ${ct.image || ""}` : "LXC"}</span>
      <span className="nx-sep" aria-hidden="true">·</span>
      <span className="nx-mono">{ct.ip || t("ct.noIp")}</span>
      <span className="nx-sep" aria-hidden="true">·</span>
      <span className="nx-mono">{ct.vcpu} vCPU · {formatSizeMb(ct.memory_mb, lang)}</span>
    </div>
  );
}
function NodeFacts({ node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const qemu = formatVersionInt(node.hypervisor_version);
  const lv = formatVersionInt(node.libvirt_version);
  return (
    <div className="nx-ometa hc-facts">
      <StatePill kind="node" wire={node.state} />
      <span>{node.id === "local" ? t("node.roleLocal") : t("node.roleMember")}</span>
      {(qemu || lv) && <><span className="nx-sep" aria-hidden="true">·</span><span className="nx-mono">{[qemu && `QEMU ${qemu}`, lv && `libvirt ${lv}`].filter(Boolean).join(" · ")}</span></>}
      {node.uptime_s ? <><span className="nx-sep" aria-hidden="true">·</span><span>{t("node.upSince", { d: formatUptimeLong(node.uptime_s, lang) })}</span></> : null}
    </div>
  );
}

// The menu of a resource page: a filter, then its pages in groups. The current page is marked.
function ResourceMenu({ type, name, tab, admin, onPage }) {
  const t = useT();
  const [q, setQ] = useState("");
  const needle = normalize(q);
  const groups = RESOURCE_MENUS[type].map((g) => ({
    group: g.group,
    pages: g.pages.map((p) => (typeof p === "string" ? { id: p } : p)).filter((p) => (!p.admin || admin || p.id === tab) && (!needle || normalize(t(resourcePageLabel(p.id))).includes(needle))),
  })).filter((g) => g.pages.length);
  return (
    <nav className="hc-rmenu" aria-label={name}>
      <label className="hc-filter hc-rmenu-find"><Search size={14} aria-hidden="true" /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("res.menuFilter")} aria-label={t("res.menuFilter")} /></label>
      {groups.map((g, i) => (
        <div key={g.group || i} className="hc-rgroup" role="group" aria-label={g.group ? t(g.group) : name}>
          {g.group && <h2>{t(g.group)}</h2>}
          {g.pages.map((p) => <button key={p.id} type="button" id={`nx-top-${p.id}`} data-preload={`${type}:${p.id}`} aria-current={tab === p.id ? "page" : undefined} onClick={() => onPage(p.id)}>{t(resourcePageLabel(p.id))}</button>)}
        </div>
      ))}
    </nav>
  );
}

export default function Workspace({ children }) {
  const t = useT();
  const activeTab = useUrlSync();
  const { selection, nodes, vms, loading } = useInfraStore(useShallow((s) => ({ selection: s.selection, nodes: s.nodes, vms: s.vms, loading: s.loading })));
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const caps = capabilities(useAuthStore((s) => s.role));
  const failing = useFreshness((s) => s.failing);
  // A cluster node that lost the quorum changes nothing, nor does a cluster updated to 1.0 while a
  // member still runs an earlier version: said above every page while it lasts, with its real cause.
  const localNode = nodes.find((n) => n.id === "local");
  const readOnly = Boolean(localNode?.read_only);
  const readOnlyText = localNode?.read_only_cause === "update_to_1_0"
    ? t("ro.update", { nodes: (localNode.read_only_waiting_for || []).join(", ") })
    : localNode?.read_only_cause === "no_daemon" ? t("ro.noDaemon") : t("ro.banner");
  const database = localNode?.database;
  const databaseBad = database?.status === "damaged" || database?.status === "failing";
  const { containers, containersLoaded } = useFreshness(useShallow((s) => ({ containers: s.containers, containersLoaded: s.containersLoaded })));

  const resource = selection.type === "vm" ? findVm(vms, selection.id)
    : selection.type === "node" ? nodes.find((n) => n.id === selection.id)
    : selection.type === "container" ? containers.find((c) => c.name === selection.id) : null;
  const isDc = selection.type === "datacenter";
  const isObj = selection.type === "node" || selection.type === "vm" || selection.type === "container";
  const { page: tab } = locate(isObj ? selection.type : "datacenter", activeTab);
  const Registry = { node: NODE_TABS, vm: VM_TABS, container: CONTAINER_TABS }[selection.type] || DATACENTER_TABS;
  const Active = Registry[tab];
  const listLoaded = selection.type === "container" ? containersLoaded : !loading;
  const missing = listLoaded && isObj && !resource;
  const vmNode = selection.type === "vm" && resource ? nodes.find((n) => n.id === resource.node) : null;
  const remote = (selection.type === "node" && resource?.distant) || vmNode?.distant;

  function setTab(id) { navigateTo(selection.type, selection.id, id); }
  const toList = () => navigateTo("datacenter", null, LIST_OF[selection.type]);
  // The tab title names what is shown: the service, or the resource and its page.
  const docTitle = !isObj ? t(serviceLabel(tab)) : missing ? t("res.notFoundTitle") : resource ? (tab === "summary" ? resource.name : `${resource.name} – ${t(resourcePageLabel(tab))}`) : "";
  useEffect(() => { setDocumentTitle(docTitle); }, [docTitle]);
  const visit = useConsoleStore((st) => st.visit);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (isObj && resource) visit(selection.type, selection.id, resource.name); }, [isObj, resource?.name, selection.type, selection.id, visit]);
  // A VM of another cluster member is backed up by that member, through this node (app/routers/backups.py); the other
  // pages still read this host only, and a node driven over SSH keeps no backups.
  const localOnly = selection.type === "vm" && isRemoteVm(resource) && LOCAL_ONLY_VM_PAGES.has(tab)
    && !(tab === "backup" && vmNode?.member);
  const pageEl = localOnly
    ? <EmptyState title={t("vm.remotePageTitle")} help={t("vm.remotePageHelp", { node: vmNode?.name || resource.node })} />
    : Active ? <div className="hc-page-in" key={`${selection.type}:${selection.id}:${tab}`}><Suspense fallback={<Loading />}><Active resource={resource} selection={selection} /></Suspense></div> : null;

  return (
    <main className="nx-main" id="nx-main" tabIndex={-1}>
      {failing && <div className="nx-banner" role="status">▲ {t("inv.stale")}</div>}
      {readOnly && <div className="nx-banner nx-banner--danger" role="alert">{readOnlyText}</div>}
      {databaseBad && <DatabaseBanner db={database} admin={caps.admin} />}
      {remote && <div className="nx-banner nx-banner--info" role="status">{t("res.remoteBanner")}</div>}
      <div className="nx-content" data-legacy="true">
        {children}
        {isDc ? (
          <div className="nx-page" id="nx-panel">
            {ADMIN_ONLY.has(tab) && !caps.admin ? (<><PageHeader title={t(serviceLabel(tab))} /><PermissionNotice requires={t("top.role.admin")} /></>)
              : loading && tab !== "summary" ? null
              : OWN_HEADER.has(tab) ? pageEl
              : (<><PageHeader title={t(serviceLabel(tab))} />{pageEl}</>)}
          </div>
        ) : !isObj ? (
          <div className="nx-page"><div className="nx-legacy"><p>{t("res.storagePlaceholder")}</p></div></div>
        ) : missing ? (
          <div className="nx-page">
            <header className="hc-ph">
              <nav className="hc-crumbs" aria-label={t("crumb.label")}>
                <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "summary")}>{t("nav.overview")}</button><ChevronRight size={12} aria-hidden="true" />
                <button type="button" className="nx-lnk" onClick={toList}>{t(serviceLabel(LIST_OF[selection.type]))}</button><ChevronRight size={12} aria-hidden="true" />
              </nav>
              <div className="hc-ph-row">
                <ServiceTile id={LIST_OF[selection.type]} size={32} />
                <div className="hc-ph-t"><div className="nx-ph-title"><h1>{t("res.notFoundTitle")}</h1></div><p className="hc-ph-sub">{selection.id}</p></div>
              </div>
            </header>
            <EmptyState title={t("res.notFound")} help={t("res.notFoundHelp")} action={(
              <span className="nx-inline">
                <button type="button" className="nx-btn nx-btn--primary" onClick={toList}>{t("res.backToList", { list: t(serviceLabel(LIST_OF[selection.type])) })}</button>
                <button type="button" className="nx-btn" onClick={() => window.dispatchEvent(new Event("nx:palette"))}>{t("res.search")}</button>
              </span>
            )} />
          </div>
        ) : resource ? (
          <div className="hc-respage">
            <header className="hc-ph hc-ph--res">
              <nav className="hc-crumbs" aria-label={t("crumb.label")}>
                <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "summary")}>{t("nav.overview")}</button><ChevronRight size={12} aria-hidden="true" />
                <button type="button" className="nx-lnk" onClick={toList}>{t(serviceLabel(LIST_OF[selection.type]))}</button><ChevronRight size={12} aria-hidden="true" />
              </nav>
              <div className="hc-ph-row">
                <ServiceTile id={LIST_OF[selection.type]} size={32} />
                <div className="hc-ph-t">
                  <div className="nx-ph-title"><h1>{resource.name}</h1>{selection.type === "vm" && resource.vmid != null && <span className="nx-vmid" title={t("vmid.title")}>{resource.vmid}</span>}{tab !== "summary" && <span className="hc-ph-page">{t(resourcePageLabel(tab))}</span>}</div>
                  <p className="hc-ph-sub">{t(`home.kind.${selection.type}`)}</p>
                </div>
                <button type="button" className="hc-ibtn hc-ph-close" aria-label={t("action.close")} title={t("action.close")} onClick={toList}><X size={18} aria-hidden="true" /></button>
              </div>
            </header>
            <div className="hc-res">
              <ResourceMenu type={selection.type} name={resource.name} tab={tab} admin={caps.admin} onPage={setTab} />
              <div className="hc-rbody">
                <div className="hc-cmd hc-cmd--res" role="toolbar" aria-label={t("actions")}>
                  {selection.type === "vm" ? <VmHeaderActions vm={resource} currentTab={tab} setTab={setTab} />
                    : selection.type === "container" ? <ContainerHeaderActions ct={resource} />
                    : <NodeHeaderActions node={resource} setTab={setTab} />}
                </div>
                {/* The overview shows these facts in its Essentials; the other pages keep them on one line. */}
                {selection.type === "vm" ? <>{tab !== "summary" && <VmFacts vm={resource} node={vmNode} />}<PendingChanges vm={resource} tab={tab} /></>
                  : tab === "summary" ? null : selection.type === "container" ? <ContainerFacts ct={resource} /> : <NodeFacts node={resource} />}
                <div className="nx-page" id="nx-panel" tabIndex={0}>
                  {loading ? null : pageEl}
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </main>
  );
}
