import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { activeService, allowedServices, serviceLabel, useConsoleStore } from "../lib/services";
import { fetchApiDocsAccess } from "../../api/client";
import ServiceTile from "../components/ServiceTile";

// Whether this person may open the API page: administrators always, the others when the documentation is open.
export function useApiDocsAccess(admin) {
  const [apiDocs, setApiDocs] = useState(false);
  useEffect(() => {
    if (admin) return undefined;
    const check = () => fetchApiDocsAccess().then((r) => setApiDocs(Boolean(r?.allowed))).catch(() => setApiDocs(false));
    check();
    window.addEventListener("nx:api-docs-access", check);
    return () => window.removeEventListener("nx:api-docs-access", check);
  }, [admin]);
  return admin || apiDocs;
}

// The service menu on the left: create, home, every resource, the catalogue, then the services this person pinned.
// Collapsed, it keeps the icons (each still named for assistive technology).
export default function SideMenu({ collapsed, drawerOpen = false, onToggleCollapse }) {
  const t = useT();
  const { selection, tab, navigateTo } = useInfraStore(useShallow((s) => ({ selection: s.selection, tab: s.activeTab, navigateTo: s.navigateTo })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const apiDocs = useApiDocsAccess(caps.admin);
  const favorites = useConsoleStore((s) => s.favorites);
  const allowed = new Set(allowedServices({ admin: caps.admin, apiDocs }).map((s) => s.id));
  const active = activeService(selection, tab);
  const go = (id) => { navigateTo("datacenter", null, id); window.dispatchEvent(new Event("nx:navigated")); };
  // On a narrow screen the menu is a drawer: opening it takes the keyboard into it, closing it gives the focus back.
  const navRef = useRef(null);
  const opener = useRef(null);
  useEffect(() => {
    if (drawerOpen) {
      opener.current = document.activeElement;
      requestAnimationFrame(() => navRef.current?.querySelector("button")?.focus());
    } else if (opener.current) {
      if (document.body.contains(opener.current) && navRef.current?.contains(document.activeElement)) opener.current.focus();
      opener.current = null;
    }
  }, [drawerOpen]);

  const item = (id) => {
    const label = t(serviceLabel(id));
    const on = active === id;
    return (
      <button key={id} type="button" className="hc-si" aria-current={on ? "page" : undefined} title={collapsed ? label : undefined} onClick={() => go(id)}>
        <ServiceTile id={id} />
        <span className="hc-si-lb">{label}</span>
      </button>
    );
  };

  return (
    <nav ref={navRef} className="hc-side" aria-label={t("nav.main")}>
      <div className="hc-side-scroll">
        {caps.create && (
          <button type="button" className="hc-si hc-si--create" aria-current={active === "create" ? "page" : undefined} title={collapsed ? t("svc.create") : undefined} onClick={() => go("create")}>
            <Plus size={20} aria-hidden="true" />
            <span className="hc-si-lb">{t("svc.create")}</span>
          </button>
        )}
        {item("summary")}
        {item("all")}
        {item("services")}
        <hr />
        <h2 className="hc-side-h">{t("svc.favorites")}</h2>
        {favorites.filter((id) => allowed.has(id)).map(item)}
        {favorites.length === 0 && <p className="hc-side-empty">{t("svc.favoritesNone")}</p>}
      </div>
      <button type="button" className="hc-si hc-side-foot" onClick={onToggleCollapse} aria-expanded={!collapsed} title={collapsed ? t("nav.expand") : undefined}>
        {collapsed ? <PanelLeftOpen size={18} aria-hidden="true" /> : <PanelLeftClose size={18} aria-hidden="true" />}
        <span className="hc-si-lb">{collapsed ? t("nav.expand") : t("nav.collapse")}</span>
      </button>
    </nav>
  );
}
