import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import StatusIndicator from "../components/StatusIndicator";
import { EmptyState } from "../components/States";
import { DATACENTER_TABS, NODE_TABS, VM_TABS, SECTIONS, sectionOfTab } from "../legacy/tabs";
import { selectionToPath, withTab } from "../lib/urls";
import { capabilities, vmActionState } from "../lib/capabilities";
import { useVmActions } from "../lib/vmActions";
import { useFreshness } from "../lib/inventory";

const VM_TAB_IDS = Object.keys(VM_TABS);
const NODE_TAB_IDS = Object.keys(NODE_TABS);

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
    const m = pathname.match(/^\/(node|vm)\/([^/]+)/);
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

  // store -> URL (selection or requested tab changed from the tree, a legacy panel, the rail...)
  useEffect(() => {
    const sel = useInfraStore.getState().selection;
    const base = selectionToPath(sel);
    if (!base) return;
    const params = new URLSearchParams(search);
    const currentTab = params.get("tab") || "summary";
    const wantedTab = pendingTab || (base === pathname ? currentTab : "summary");
    const target = withTab(base, wantedTab);
    if (pendingTab) clearPendingTab();
    if (target !== `${pathname}${search}`) navigate(target);
    if (useInfraStore.getState().activeTab !== wantedTab) setActiveTab(wantedTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.type, selection.id, pendingTab]);
  return activeTab;
}

function tabsFor(selection, section) {
  if (selection.type === "node") return NODE_TAB_IDS;
  if (selection.type === "vm") return VM_TAB_IDS;
  return SECTIONS[section] || SECTIONS.overview;
}

export default function Workspace({ children }) {
  const t = useT();
  const activeTab = useUrlSync();
  const { selection, nodes, vms, loading } = useInfraStore(useShallow((s) => ({ selection: s.selection, nodes: s.nodes, vms: s.vms, loading: s.loading })));
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  const vmActions = useVmActions();
  const failing = useFreshness((s) => s.failing);

  const resource = selection.type === "vm" ? vms.find((v) => v.nom === selection.id)
    : selection.type === "node" ? nodes.find((n) => n.id === selection.id) : null;

  const isDc = selection.type === "datacenter";
  const section = sectionOfTab(activeTab);
  const tabIds = tabsFor(selection, isDc ? section : null);
  const tab = tabIds.includes(activeTab) ? activeTab : tabIds[0];
  const Registry = selection.type === "node" ? NODE_TABS : selection.type === "vm" ? VM_TABS : DATACENTER_TABS;
  const Active = Registry[tab];

  const title = isDc ? t("res.datacenter") : selection.type === "node" ? (resource?.nom || selection.id) : selection.id;
  const kindLabel = t(`res.${selection.type === "container" ? "container" : selection.type}`);
  const missing = !loading && (selection.type === "vm" || selection.type === "node") && !resource;

  function setTab(id) { navigateTo(selection.type, selection.id, id); }

  function onTabKeyDown(e) {
    const i = tabIds.indexOf(tab);
    let n = null;
    if (e.key === "ArrowRight") n = tabIds[(i + 1) % tabIds.length];
    else if (e.key === "ArrowLeft") n = tabIds[(i - 1 + tabIds.length) % tabIds.length];
    else if (e.key === "Home") n = tabIds[0];
    else if (e.key === "End") n = tabIds[tabIds.length - 1];
    if (n) { e.preventDefault(); setTab(n); requestAnimationFrame(() => document.getElementById(`nx-tab-${n}`)?.focus()); }
  }

  const vm = selection.type === "vm" ? resource : null;
  const act = (a) => vmActionState(a, vm, caps);

  return (
    <main className="nx-main" id="nx-main" tabIndex={-1}>
      {failing && <div className="nx-banner" role="status">▲ {t("inv.stale")}</div>}
      {selection.type === "node" && resource?.distant && <div className="nx-banner nx-banner--info" role="status">{t("res.remoteBanner")}</div>}
      {selection.type === "vm" && resource && nodes.find((n) => n.id === resource.node)?.distant && <div className="nx-banner nx-banner--info" role="status">{t("res.remoteBanner")}</div>}

      <div className="nx-reshead">
        <h1>{title}</h1>
        {resource?.etat && <StatusIndicator kind={selection.type === "node" ? "node" : "vm"} wire={resource.etat} />}
        <span className="nx-meta">{kindLabel}{vm?.node ? ` · ${nodes.find((n) => n.id === vm.node)?.nom || vm.node}` : ""}{vm?.ip ? ` · ` : ""}{vm?.ip && <span className="nx-mono">{vm.ip}</span>}</span>
        {vm && (
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: "var(--space-2)" }}>
            {[["start", "menu.start"], ["stop", "menu.stop"], ["restart", "menu.restart"]].map(([a, k]) => {
              const s = act(a);
              return <button key={a} type="button" className="nx-btn" aria-disabled={!s.enabled || undefined} title={!s.enabled && s.reason ? t(s.reason) : undefined} onClick={() => s.enabled && vmActions.run(vm, a)}>{t(k)}{!s.enabled && s.reason && <span className="nx-sr"> — {t(s.reason)}</span>}</button>;
            })}
            <button type="button" className="nx-btn" aria-disabled={!act("console").enabled || undefined} title={!act("console").enabled ? t(act("console").reason) : undefined} onClick={() => act("console").enabled && vmActions.openConsole(vm)}>{t("menu.console")}</button>
          </span>
        )}
      </div>

      {!missing && selection.type !== "storage" && selection.type !== "container" && (
        <div className="nx-tabs" role="tablist" aria-label={title} onKeyDown={onTabKeyDown}>
          {tabIds.map((id) => (
            <button key={id} id={`nx-tab-${id}`} type="button" role="tab" aria-selected={tab === id} aria-controls="nx-panel" tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)}>{t(`tab.${id}`)}</button>
          ))}
        </div>
      )}

      <div className="nx-content" data-legacy="true" id="nx-panel" role="tabpanel" aria-labelledby={`nx-tab-${tab}`} tabIndex={0}>
        {children}
        {missing ? (
          <EmptyState title={t("res.notFound")} help={t("res.notFoundHelp")} action={<button type="button" className="nx-btn" onClick={() => navigateTo("datacenter", null, "summary")}>{t("crumb.datacenter")}</button>} />
        ) : selection.type === "storage" ? (
          <div className="nx-legacy"><p>{t("res.storagePlaceholder")}</p></div>
        ) : loading ? null : Active ? (
          <>
            <div className="nx-legacy-note">{t("legacy.note")}</div>
            <div className="nx-legacy"><Active resource={resource} selection={selection} /></div>
          </>
        ) : null}
      </div>
    </main>
  );
}
