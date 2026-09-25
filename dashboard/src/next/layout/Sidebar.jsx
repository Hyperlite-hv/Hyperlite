import { useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore, LANGS } from "../i18n";
import { useThemeStore } from "../tokens/theme";
import { capabilities } from "../lib/capabilities";
import { useFreshness } from "../lib/inventory";
import { deriveAlerts, summarizeHealth } from "../lib/alerts";
import Menu, { MenuItem } from "../components/Menu";
import UpdateModal from "../../components/UpdateModal";
import AccountSecurityModal from "../../components/AccountSecurityModal";

function Icon({ d, size = 17 }) {
  return <svg width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d={d} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

const ICONS = {
  overview: "M3 4h6v6H3zM11 4h6v4h-6zM11 10h6v6h-6zM3 12h6v4H3z",
  infra: "M3 6l7-3 7 3-7 3-7-3zM3 6v8l7 3 7-3V6M10 9v8",
  cluster: "M4 4h5v5H4zM11 4h5v5h-5zM4 11h5v5H4zM11 11h5v5h-5z",
  nodes: "M3 3.5h14v5H3zM3 11.5h14v5H3zM6 6h.01M6 14h.01",
  vms: "M3 4h14v9H3zM7 17h6M10 13v4",
  containers: "M10 3l6 3.4v7.2L10 17l-6-3.4V6.4L10 3zM4 6.5l6 3.3 6-3.3M10 9.8V17",
  storage: "M3 5.5c0-1.4 3.1-2.5 7-2.5s7 1.1 7 2.5-3.1 2.5-7 2.5-7-1.1-7-2.5zM3 5.5v9c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-9M3 10c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5",
  network: "M10 3v3M10 14v3M4.5 6l2.5 1.5M13 12.5l2.5 1.5M4.5 14l2.5-1.5M13 7.5l2.5-1.5M10 10m-3 0a3 3 0 106 0 3 3 0 10-6 0",
  iso: "M3 4h14v12H3zM3 8h14M7 4v4",
  snapshots: "M3.5 6h3l1.3-1.5h4.4L13.5 6h3v9h-13zM10 10.5a2.3 2.3 0 100 4.6 2.3 2.3 0 000-4.6z",
  backups: "M4 3.5h12v13H4zM4 7h12M4 10.5h12M4 14h12M7 3.5v13",
  exports: "M10 3v9M6.5 8.5L10 12l3.5-3.5M4 14.5v1.8h12v-1.8",
  automation: "M10 3v2.2M10 14.8V17M3 10h2.2M14.8 10H17M5.5 5.5l1.5 1.5M13 13l1.5 1.5M14.5 5.5L13 7M7 13l-1.5 1.5M10 6.8a3.2 3.2 0 100 6.4 3.2 3.2 0 000-6.4z",
  monitoring: "M3 15l4-6 3 3 6-8M10 4h6v6",
  alerts: "M10 3a5.5 5.5 0 00-5.5 5.5c0 4-1.5 5-1.5 5h14s-1.5-1-1.5-5A5.5 5.5 0 0010 3zM8.2 16.5a1.8 1.8 0 003.6 0",
  logs: "M4 3.5h9l3 3v10H4zM13 3.5V7h3M7 10.5h6M7 13.5h6",
  users: "M7.2 9.2a2.7 2.7 0 100-5.4 2.7 2.7 0 000 5.4zM2.5 16c.5-3 2.4-4.6 4.7-4.6S11.4 13 11.9 16M14 9.2a2.3 2.3 0 100-4.6M13.2 11.6c1.9.3 3.3 1.7 3.7 4.4",
  settings: "M10 12.7a2.7 2.7 0 100-5.4 2.7 2.7 0 000 5.4zM10 3v1.7M10 15.3V17M17 10h-1.7M4.7 10H3M15.1 4.9l-1.2 1.2M6.1 13.9l-1.2 1.2M15.1 15.1l-1.2-1.2M6.1 6.1L4.9 4.9",
};

function NavItem({ icon, label, count, tone, active, onClick, indent, disabled, title }) {
  return (
    <button type="button" className={`nx-nav-item${active ? " active" : ""}${indent ? " nx-nav-item--sub" : ""}`} aria-current={active ? "page" : undefined} aria-disabled={disabled || undefined} title={title} onClick={disabled ? undefined : onClick}>
      {!indent && <Icon d={ICONS[icon]} />}
      <span className="nx-nav-label">{label}</span>
      {count != null && <span className={`nx-nav-count${tone ? ` nx-nav-count--${tone}` : ""}`}>{count}</span>}
    </button>
  );
}

export default function Sidebar({ collapsed }) {
  const t = useT();
  const { selection, nodes, vms, storagePools, tasks } = useInfraStore(useShallow((s) => ({ selection: s.selection, nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, tasks: s.tasks })));
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const { containers } = useFreshness(useShallow((s) => ({ containers: s.containers })));
  const role = useAuthStore((s) => s.role);
  const username = useAuthStore((s) => s.username);
  const logout = useAuthStore((s) => s.logout);
  const caps = capabilities(role);
  const lang = useLangStore((s) => s.lang);
  const setLang = useLangStore((s) => s.setLang);
  const { mode, setMode } = useThemeStore(useShallow((s) => ({ mode: s.mode, setMode: s.setMode })));
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [updateOpen, setUpdateOpen] = useState(false);
  const [securityOpen, setSecurityOpen] = useState(false);
  const userBtn = useRef(null);

  const tab = useInfraStore((s) => s.activeTab);
  const onDatacenterTab = (id) => selection.type === "datacenter" && tab === id;
  const goto = (dcTab) => navigateTo("datacenter", null, dcTab);
  const activeNode = selection.type === "node" ? nodes.find((n) => n.id === selection.id) : null;

  const health = summarizeHealth(deriveAlerts({ nodes, vms, storagePools, tasks }));

  return (
    <nav className={`nx-sidebar${collapsed ? " collapsed" : ""}`} aria-label={t("nav.main")}>
      <div className="nx-sidebar-brand">
        <span className="nx-brand-mark"><svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="2" width="16" height="16" rx="4" fill="var(--color-accent)" /><path d="M6 6v8M14 6v8M6 10h8" stroke="var(--color-text-on-accent)" strokeWidth="1.8" strokeLinecap="round" /></svg></span>
        <span className="nx-brand-text"><strong>{t("app.name")}</strong><small>{t("app.tagline")}</small></span>
      </div>

      <div className="nx-nav-scroll">
        <div className="nx-nav-group">
          <div className="nx-nav-group-label">{t("nav.group.infrastructure")}</div>
          <NavItem icon="overview" label={t("nav.overview")} active={selection.type === "datacenter" && tab === "summary"} onClick={() => goto("summary")} />
          <NavItem icon="infra" label={t("nav.infrastructure")} active={onDatacenterTab("compat")} onClick={() => goto("compat")} />
          <NavItem icon="cluster" label={t("nav.group.cluster")} active={onDatacenterTab("ha")} onClick={() => goto("ha")} />
          <NavItem icon="nodes" label={t("nav.nodes")} count={nodes.length} active={onDatacenterTab("nodes")} onClick={() => goto("nodes")} />
          {activeNode && <NavItem label={activeNode.nom} active indent />}
        </div>

        <div className="nx-nav-group">
          <div className="nx-nav-group-label">{t("nav.group.management")}</div>
          <NavItem icon="vms" label={t("nav.vms")} count={vms.length} active={selection.type === "vm"} disabled title={t("nav.comingSoon")} />
          <NavItem icon="containers" label={t("nav.containers")} count={containers?.length} active={onDatacenterTab("containers")} onClick={() => goto("containers")} />
          <NavItem icon="storage" label={t("nav.storage")} active={onDatacenterTab("storage")} onClick={() => goto("storage")} />
          <NavItem icon="network" label={t("nav.network")} active={onDatacenterTab("reseau")} onClick={() => goto("reseau")} />
          <NavItem icon="iso" label={t("nav.isoTemplates")} active={onDatacenterTab("templates")} onClick={() => goto("templates")} />
          <NavItem icon="snapshots" label={t("nav.snapshots")} disabled title={t("nav.comingSoon")} />
          <NavItem icon="backups" label={t("nav.backups")} active={onDatacenterTab("backups")} onClick={() => goto("backups")} />
          <NavItem icon="exports" label={t("nav.exports")} active={onDatacenterTab("exports")} onClick={() => goto("exports")} />
          <NavItem icon="automation" label={t("nav.automation")} active={onDatacenterTab("automation")} onClick={() => goto("automation")} />
        </div>

        <div className="nx-nav-group">
          <div className="nx-nav-group-label">{t("nav.group.observability")}</div>
          <NavItem icon="monitoring" label={t("nav.monitoring")} active={onDatacenterTab("activity")} onClick={() => goto("activity")} />
          <NavItem icon="alerts" label={t("nav.alerts")} count={health.total || null} tone={health.level === "critical" ? "danger" : "warning"} active={false} onClick={() => window.dispatchEvent(new CustomEvent("nx:dock", { detail: "alerts" }))} />
          <NavItem icon="logs" label={t("nav.systemLogs")} active={onDatacenterTab("journal")} onClick={() => goto("journal")} />
        </div>

        {caps.admin && (
          <div className="nx-nav-group">
            <div className="nx-nav-group-label">{t("nav.group.administration")}</div>
            <NavItem icon="users" label={t("nav.usersRoles")} active={onDatacenterTab("permissions")} onClick={() => goto("permissions")} />
            <NavItem icon="settings" label={t("nav.settings")} active={onDatacenterTab("notifications")} onClick={() => goto("notifications")} />
          </div>
        )}
      </div>

      <div className="nx-relative">
        <button ref={userBtn} type="button" className="nx-sidebar-user" aria-haspopup="menu" aria-expanded={userMenuOpen} onClick={() => setUserMenuOpen((o) => !o)}>
          <span className="nx-avatar">{(username || "?").slice(0, 2).toUpperCase()}</span>
          <span className="nx-user-info">
            <strong>{username}</strong>
            <small>{caps.admin ? t("top.role.admin") : t("top.role.observer")}</small>
          </span>
          <span className="nx-user-dot" aria-hidden="true" />
        </button>
        <Menu open={userMenuOpen} onClose={() => setUserMenuOpen(false)} label={username} returnFocusRef={userBtn} style={{ bottom: "calc(100% + 4px)", left: "var(--space-3)", right: "var(--space-3)" }}>
          <div className="nx-menu-label">{t("top.language")}</div>
          {LANGS.map((l) => <MenuItem key={l.id} onSelect={() => setLang(l.id)}>{l.label}{lang === l.id ? " ✓" : ""}</MenuItem>)}
          <hr />
          <div className="nx-menu-label">{t("top.theme")}</div>
          {["dark", "light", "system"].map((m) => <MenuItem key={m} onSelect={() => setMode(m)}>{t(`top.theme.${m}`)}{mode === m ? " ✓" : ""}</MenuItem>)}
          <hr />
          {caps.admin && <MenuItem onSelect={() => { setUserMenuOpen(false); setUpdateOpen(true); }}>{t("top.updates")}</MenuItem>}
          <MenuItem onSelect={() => { setUserMenuOpen(false); setSecurityOpen(true); }}>{t("top.security")}</MenuItem>
          <MenuItem onSelect={() => { localStorage.setItem("hyperlite-ui", "legacy"); window.location.reload(); }}>{t("top.legacy")}</MenuItem>
          <hr />
          <MenuItem danger onSelect={logout}>{t("top.signout")}</MenuItem>
        </Menu>
      </div>

      <UpdateModal open={updateOpen} onClose={() => setUpdateOpen(false)} triggerRef={userBtn} />
      <AccountSecurityModal open={securityOpen} onClose={() => setSecurityOpen(false)} triggerRef={userBtn} />
    </nav>
  );
}
