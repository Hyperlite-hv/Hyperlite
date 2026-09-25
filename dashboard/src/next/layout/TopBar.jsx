import { useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import Menu, { MenuItem } from "../components/Menu";
import CreateVmWizard from "../wizard/CreateVmWizard";
import CreateContainerWizard from "../wizard/CreateContainerWizard";
import { locate } from "../legacy/tabs";
import { useAuthStore } from "../../store/useAuthStore";

function Icon({ d, size = 16 }) {
  return <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true"><path d={d} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function Dropdown({ label, trigger, children, align = "right", className = "nx-btn nx-btn--ghost", disabled }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  return (
    <span className="nx-relative">
      <button ref={ref} type="button" className={className} aria-haspopup="menu" aria-expanded={open} aria-label={label} disabled={disabled} onClick={() => setOpen((o) => !o)}>{trigger}</button>
      <Menu open={open} onClose={() => setOpen(false)} label={label} returnFocusRef={ref} style={{ top: "calc(100% + 4px)", [align]: 0 }}>
        {typeof children === "function" ? children(() => setOpen(false)) : children}
      </Menu>
    </span>
  );
}

// The account menu (language, theme, updates, security, sign out) lives in the sidebar's user
// block now, not here — the top bar stays limited to navigation and the current page's actions.
export default function TopBar({ onOpenPalette, onToggleSidebar, wizards, setWizards }) {
  const t = useT();
  const { tasks, nodes, selection, activeTab } = useInfraStore(useShallow((s) => ({ tasks: s.tasks, nodes: s.nodes, selection: s.selection, activeTab: s.activeTab })));
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);

  const crumbNode = selection.type === "node" ? nodes.find((n) => n.id === selection.id) : null;
  const isOverview = selection.type === "datacenter" && (activeTab || "summary") === "summary";
  const crumbMid = selection.type === "node" ? t("nav.nodes") : selection.type === "vm" ? t("nav.vms") : isOverview ? t("nav.group.infrastructure") : t("res.datacenter");
  const crumbLast = selection.type === "node" ? (crumbNode?.nom || selection.id)
    : selection.type === "vm" ? selection.id
    : isOverview ? t("nav.overview")
    : t(`tab.${locate("datacenter", activeTab || "summary").page}`);

  const vmBtn = useRef(null);
  const ctBtn = useRef(null);
  const running = tasks.filter((x) => x.statut === "en_cours").length;
  const collapsed = useInfraStore((s) => s.taskLogCollapsed);
  const toggleTaskLog = useInfraStore((s) => s.toggleTaskLog);

  return (
    <header className="nx-top">
      <button type="button" className="nx-btn nx-btn--icon nx-btn--ghost" aria-label={t("nav.toggleSidebar")} onClick={onToggleSidebar}>
        <Icon d="M2 3.5h12v9H2zM6.5 3.5v9" />
      </button>
      <nav className="nx-crumbs nx-hide-narrow" aria-label="Breadcrumb">
        <span>{t("app.name")}</span><span aria-hidden="true">›</span>
        <span>{crumbMid}</span><span aria-hidden="true">›</span>
        <span className="nx-crumb-current" aria-current="page">{crumbLast}</span>
      </nav>
      <span className="nx-top-spacer" />
      <button type="button" className="nx-find" onClick={onOpenPalette} aria-label={t("find.open")}>
        <Icon d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM11 11l3.5 3.5" />
        <span className="nx-find-label">{t("find.placeholder")}</span>
        <span className="nx-kbd" aria-hidden="true">Ctrl K</span>
      </button>
      <span className="nx-top-group">
        {caps.create && (
          <Dropdown label={t("action.create")} className="nx-btn nx-btn--primary" trigger={<><Icon d="M8 3v10M3 8h10" /><span className="nx-hide-narrow">{t("action.create")}</span></>}>
            {(close) => (<>
              <MenuItem onSelect={() => { close(); setWizards({ vm: true }); }}>{t("action.createVm")}</MenuItem>
              <MenuItem onSelect={() => { close(); setWizards({ container: true }); }}>{t("action.createContainer")}</MenuItem>
            </>)}
          </Dropdown>
        )}
        <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={`${t("top.tasks")}${running ? ` (${running})` : ""}`} aria-pressed={!collapsed} onClick={toggleTaskLog} style={{ position: "relative" }}>
          <Icon d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01" />
          {running > 0 && <span className="nx-badge-count" style={{ position: "absolute", top: -2, right: -2 }}>{running}</span>}
        </button>
      </span>

      <CreateVmWizard open={!!wizards.vm} onClose={() => setWizards({})} triggerRef={vmBtn} />
      <CreateContainerWizard open={!!wizards.container} onClose={() => setWizards({})} triggerRef={ctBtn} />
    </header>
  );
}
