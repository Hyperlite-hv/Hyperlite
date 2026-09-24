import { useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore, LANGS } from "../i18n";
import { useThemeStore } from "../tokens/theme";
import { useFreshness } from "../lib/inventory";
import { capabilities } from "../lib/capabilities";
import { relativeTime } from "../lib/format";
import { deriveAlerts, summarizeHealth } from "../lib/alerts";
import StatusIndicator from "../components/StatusIndicator";
import Menu, { MenuItem } from "../components/Menu";
import VMWizard from "../../wizard/VMWizard";
import ContainerWizard from "../../wizard/ContainerWizard";
import UpdateModal from "../../components/UpdateModal";
import AccountSecurityModal from "../../components/AccountSecurityModal";

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

export default function TopBar({ onOpenPalette, onToggleInventory, inventoryOpen, wizards, setWizards }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const setLang = useLangStore((s) => s.setLang);
  const { mode, setMode } = useThemeStore(useShallow((s) => ({ mode: s.mode, setMode: s.setMode })));
  const { tasks, nodes, vms, storagePools } = useInfraStore(useShallow((s) => ({ tasks: s.tasks, nodes: s.nodes, vms: s.vms, storagePools: s.storagePools })));
  const health = summarizeHealth(deriveAlerts({ nodes, vms, storagePools, tasks }));
  const healthInfo = health.level === "critical" ? { key: "health.crit", shape: "diamond", tone: "danger" } : health.level === "attention" ? { key: "health.warn", shape: "triangle", tone: "warning" } : { key: "health.ok", shape: "dot", tone: "success" };
  const username = useAuthStore((s) => s.username);
  const role = useAuthStore((s) => s.role);
  const logout = useAuthStore((s) => s.logout);
  const caps = capabilities(role);
  const updatedAt = useFreshness((s) => s.updatedAt);
  const [updateOpen, setUpdateOpen] = useState(false);
  const [securityOpen, setSecurityOpen] = useState(false);
  const vmBtn = useRef(null);
  const ctBtn = useRef(null);
  const updBtn = useRef(null);
  const secBtn = useRef(null);
  const running = tasks.filter((x) => x.statut === "en_cours").length;
  const collapsed = useInfraStore((s) => s.taskLogCollapsed);
  const toggleTaskLog = useInfraStore((s) => s.toggleTaskLog);

  return (
    <header className="nx-top">
      <button type="button" className="nx-btn nx-btn--icon nx-only-narrow" aria-label={t("inv.open")} aria-expanded={inventoryOpen} aria-controls="nx-inventory" onClick={onToggleInventory}>
        <Icon d="M2 4h12M2 8h12M2 12h12" />
      </button>
      <span className="nx-brand"><svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="2" width="16" height="16" rx="4" fill="var(--color-accent)" /><path d="M6 6v8M14 6v8M6 10h8" stroke="var(--color-text-on-accent)" strokeWidth="1.8" strokeLinecap="round" /></svg><span className="nx-hide-narrow">{t("app.name")}</span></span>
      <span className="nx-top-spacer" />
      <button type="button" className="nx-btn nx-btn--ghost nx-health" aria-label={`${t("health.label")}: ${t(healthInfo.key, { n: health.level === "critical" ? health.critical : health.attention })}`} onClick={() => window.dispatchEvent(new CustomEvent("nx:dock", { detail: "alerts" }))}>
        <StatusIndicator override={healthInfo} compact />
        <span className="nx-hide-narrow">{t(healthInfo.key, { n: health.level === "critical" ? health.critical : health.attention })}</span>
      </button>
      <span className="nx-fresh nx-hide-narrow" role="status">{updatedAt ? t("top.updated", { t: relativeTime(updatedAt, lang) }) : ""}</span>
      <button type="button" className="nx-find" onClick={onOpenPalette} aria-label={t("find.open")}>
        <Icon d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM11 11l3.5 3.5" />
        <span className="nx-find-label">{t("find.placeholder")}</span>
        <span className="nx-kbd" aria-hidden="true">Ctrl K</span>
      </button>
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
      <Dropdown label={t("top.language")} trigger={<span className="nx-mono">{lang.toUpperCase()}</span>}>
        {(close) => LANGS.map((l) => <MenuItem key={l.id} onSelect={() => { close(); setLang(l.id); }}>{l.label}{lang === l.id ? " ✓" : ""}</MenuItem>)}
      </Dropdown>
      <Dropdown label={t("top.theme")} trigger={<Icon d="M8 2.5a5.5 5.5 0 1 0 5.5 5.5A4 4 0 0 1 8 2.5z" />}>
        {(close) => ["dark", "light", "system"].map((m) => <MenuItem key={m} onSelect={() => { close(); setMode(m); }}>{t(`top.theme.${m}`)}{mode === m ? " ✓" : ""}</MenuItem>)}
      </Dropdown>
      <Dropdown label={t("top.user")} trigger={<><Icon d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 14c.5-3 2.8-4.5 5.5-4.5s5 1.5 5.5 4.5" /><span className="nx-hide-narrow">{username}</span></>}>
        {(close) => (<>
          <div className="nx-menu-label">{username} · {caps.admin ? t("top.role.admin") : t("top.role.observer")}</div>
          {caps.admin && <MenuItem onSelect={() => { close(); setUpdateOpen(true); }}>{t("top.updates")}</MenuItem>}
          <MenuItem onSelect={() => { close(); setSecurityOpen(true); }}>{t("top.security")}</MenuItem>
          <MenuItem onSelect={() => { close(); localStorage.setItem("hyperlite-ui", "legacy"); window.location.reload(); }}>{t("top.legacy")}</MenuItem>
          <hr />
          <MenuItem danger onSelect={() => { close(); logout(); }}>{t("top.signout")}</MenuItem>
        </>)}
      </Dropdown>

      <VMWizard open={!!wizards.vm} onClose={() => setWizards({})} triggerRef={vmBtn} />
      <ContainerWizard open={!!wizards.container} onClose={() => setWizards({})} triggerRef={ctBtn} />
      <UpdateModal open={updateOpen} onClose={() => setUpdateOpen(false)} triggerRef={updBtn} />
      <AccountSecurityModal open={securityOpen} onClose={() => setSecurityOpen(false)} triggerRef={secBtn} />
    </header>
  );
}
