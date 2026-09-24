import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import StatusIndicator from "../components/StatusIndicator";
import { EmptyState } from "../components/States";
import { DATACENTER_TABS, NODE_TABS, VM_TABS, locate } from "../legacy/tabs";
import Menu, { MenuItem } from "../components/Menu";
import { selectionToPath, withTab } from "../lib/urls";
import { capabilities, vmActionState } from "../lib/capabilities";
import { useVmActions } from "../lib/vmActions";
import { useFreshness } from "../lib/inventory";


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

const OBJ_ICON = {
  datacenter: <g fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 17V6l7-3 7 3v11zM7 17v-4h6v4M7 8.5h.01M10 8.5h.01M13 8.5h.01" strokeLinecap="round" strokeLinejoin="round" /></g>,
  node: <g fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="3" width="14" height="5.5" rx="1" /><rect x="3" y="11.5" width="14" height="5.5" rx="1" /><path d="M6 5.8h.01M6 14.2h.01" strokeLinecap="round" /></g>,
  vm: <g fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2.5" y="3.5" width="15" height="10" rx="1.2" /><path d="M7 17h6M10 13.5V17" strokeLinecap="round" /></g>,
};

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
  const hasTabs = selection.type === "datacenter" || selection.type === "node" || selection.type === "vm";
  const { tabs: topTabs, top, page: tab } = locate(hasTabs ? selection.type : "datacenter", activeTab);
  const Registry = selection.type === "node" ? NODE_TABS : selection.type === "vm" ? VM_TABS : DATACENTER_TABS;
  const Active = Registry[tab];

  const title = isDc ? t("res.datacenter") : selection.type === "node" ? (resource?.nom || selection.id) : selection.id;
  const kindLabel = t(`res.${selection.type === "container" ? "container" : selection.type}`);
  const missing = !loading && (selection.type === "vm" || selection.type === "node") && !resource;

  function setTab(id) { navigateTo(selection.type, selection.id, id); }
  const goTop = (tt) => setTab(tt.pages[0].page);

  function onTabKeyDown(e) {
    const i = topTabs.findIndex((x) => x.id === top.id);
    let n = null;
    if (e.key === "ArrowRight") n = topTabs[(i + 1) % topTabs.length];
    else if (e.key === "ArrowLeft") n = topTabs[(i - 1 + topTabs.length) % topTabs.length];
    else if (e.key === "Home") n = topTabs[0];
    else if (e.key === "End") n = topTabs[topTabs.length - 1];
    if (n) { e.preventDefault(); goTop(n); requestAnimationFrame(() => document.getElementById(`nx-top-${n.id}`)?.focus()); }
  }

  const vm = selection.type === "vm" ? resource : null;
  const act = (a) => vmActionState(a, vm, caps);
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsBtn = useRef(null);
  const wizard = (kind) => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: kind }));
  const copy = (text) => navigator.clipboard?.writeText(text);
  const offlineNode = selection.type === "node" && resource && resource.etat !== "online";
  const useSubnav = hasTabs && top.pages.length > 1;

  return (
    <main className="nx-main" id="nx-main" tabIndex={-1}>
      {failing && <div className="nx-banner" role="status">▲ {t("inv.stale")}</div>}
      {selection.type === "node" && resource?.distant && <div className="nx-banner nx-banner--info" role="status">{t("res.remoteBanner")}</div>}
      {selection.type === "vm" && resource && nodes.find((n) => n.id === resource.node)?.distant && <div className="nx-banner nx-banner--info" role="status">{t("res.remoteBanner")}</div>}

      {(selection.type === "node" || selection.type === "vm") && (
        <nav className="nx-crumbrow" aria-label="Breadcrumb">
          <button type="button" onClick={() => navigateTo("datacenter", null, "summary")}>{t("crumb.datacenter")}</button>
          {selection.type === "vm" && resource && (<><span aria-hidden="true">›</span><button type="button" onClick={() => navigateTo("node", resource.node, "summary")}>{nodes.find((n) => n.id === resource.node)?.nom || resource.node}</button></>)}
          <span aria-hidden="true">›</span><span aria-current="page">{title}</span>
        </nav>
      )}
      <div className="nx-reshead">
        <svg className="nx-objicon" width="22" height="22" viewBox="0 0 20 20" aria-hidden="true">{OBJ_ICON[selection.type] || OBJ_ICON.datacenter}</svg>
        <h1 style={offlineNode ? { fontStyle: "italic" } : undefined}>{title}</h1>
        {offlineNode && <span className="nx-tone-warning" role="status">▲ {t("res.offlineNode")}</span>}
        {resource?.etat && <StatusIndicator kind={selection.type === "node" ? "node" : "vm"} wire={resource.etat} />}
        <span className="nx-meta">{kindLabel}{vm?.node ? ` · ${nodes.find((n) => n.id === vm.node)?.nom || vm.node}` : ""}{vm?.ip ? ` · ` : ""}{vm?.ip && <span className="nx-mono">{vm.ip}</span>}</span>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: "var(--space-2)", alignItems: "center" }}>
          {vm && (() => { const c = act("console"); const st = act("start");
            return vm.etat === "actif"
              ? <button type="button" className="nx-btn nx-btn--primary" aria-disabled={!c.enabled || undefined} onClick={() => c.enabled && vmActions.openConsole(vm)}>{t("actions.primary.console")}</button>
              : <button type="button" className="nx-btn nx-btn--primary" aria-disabled={!st.enabled || undefined} title={!st.enabled && st.reason ? t(st.reason) : undefined} onClick={() => st.enabled && vmActions.run(vm, "start")}>{t("menu.start")}{!st.enabled && st.reason && <span className="nx-sr"> — {t(st.reason)}</span>}</button>; })()}
          <span className="nx-relative">
            <button ref={actionsBtn} type="button" className="nx-btn" aria-haspopup="menu" aria-expanded={actionsOpen} onClick={() => setActionsOpen((o) => !o)}>{t("actions")} <span aria-hidden="true">▾</span></button>
            <Menu open={actionsOpen} onClose={() => setActionsOpen(false)} label={t("actions")} returnFocusRef={actionsBtn} style={{ top: "calc(100% + 4px)", right: 0 }}>
              {vm && ["start", "stop", "restart", "console"].map((a) => { const s2 = act(a); return <MenuItem key={a} disabled={!s2.enabled} reason={s2.reason ? t(s2.reason) : undefined} onSelect={() => { setActionsOpen(false); if (a === "console") vmActions.openConsole(vm); else vmActions.run(vm, a); }}>{t(`menu.${a}`)}</MenuItem>; })}
              {vm?.ip && <MenuItem onSelect={() => { setActionsOpen(false); copy(vm.ip); }}>{t("menu.copyIp")}</MenuItem>}
              {(isDc && caps.create) && (<>
                <MenuItem onSelect={() => { setActionsOpen(false); wizard("vm"); }}>{`${t("action.create")} · ${t("action.createVm")}`}</MenuItem>
                <MenuItem onSelect={() => { setActionsOpen(false); wizard("container"); }}>{`${t("action.create")} · ${t("action.createContainer")}`}</MenuItem>
              </>)}
              <MenuItem onSelect={() => { setActionsOpen(false); copy(window.location.href); }}>{t("menu.copyLink")}</MenuItem>
            </Menu>
          </span>
        </span>
      </div>

      {!missing && hasTabs && (
        <div className="nx-tabs" role="tablist" aria-label={title} onKeyDown={onTabKeyDown}>
          {topTabs.map((x) => (
            <button key={x.id} id={`nx-top-${x.id}`} type="button" role="tab" aria-selected={top.id === x.id} tabIndex={top.id === x.id ? 0 : -1} onClick={() => goTop(x)}>{t(x.label)}</button>
          ))}
        </div>
      )}

      <div className={useSubnav && !missing ? "nx-split" : "nx-splitless"}>
        {useSubnav && !missing && (
          <div className="nx-subnav" role="tablist" aria-orientation="vertical" aria-label={t("sub.nav")} onKeyDown={(e) => {
            const ids = top.pages.map((p) => p.page); const i = ids.indexOf(tab); let n = null;
            if (e.key === "ArrowDown") n = ids[(i + 1) % ids.length]; else if (e.key === "ArrowUp") n = ids[(i - 1 + ids.length) % ids.length];
            if (n) { e.preventDefault(); setTab(n); requestAnimationFrame(() => document.getElementById(`nx-tab-${n}`)?.focus()); }
          }}>
            {top.pages.map((p) => (
              <div key={p.page} role="presentation">
                {p.group && <div className="nx-subnav-group" role="presentation">{t(p.group)}</div>}
                <button id={`nx-tab-${p.page}`} type="button" role="tab" aria-selected={tab === p.page} aria-controls="nx-panel" tabIndex={tab === p.page ? 0 : -1} onClick={() => setTab(p.page)}>{t(p.label || `tab.${p.page}`)}</button>
              </div>
            ))}
          </div>
        )}
        <div className="nx-content" data-legacy="true" id="nx-panel" role="tabpanel" aria-labelledby={hasTabs ? (useSubnav ? `nx-tab-${tab}` : `nx-top-${top.id}`) : undefined} tabIndex={0}>
          {children}
          {missing ? (
            <EmptyState title={t("res.notFound")} help={t("res.notFoundHelp")} action={<button type="button" className="nx-btn" onClick={() => navigateTo("datacenter", null, "summary")}>{t("crumb.datacenter")}</button>} />
          ) : selection.type === "storage" ? (
            <div className="nx-legacy"><p>{t("res.storagePlaceholder")}</p></div>
          ) : loading ? null : Active ? (
            <>
              {useSubnav && <h2 className="nx-pagetitle">{t(top.pages.find((x) => x.page === tab)?.label || `tab.${tab}`)}</h2>}
              <div className="nx-legacy"><Active resource={resource} selection={selection} /></div>
            </>
          ) : null}
        </div>
      </div>
    </main>
  );
}
