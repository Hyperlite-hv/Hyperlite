import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Bell, Menu as MenuIcon, Search, Settings } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore, LANGS } from "../i18n";
import { useThemeStore } from "../tokens/theme";
import { capabilities } from "../lib/capabilities";
import { deriveAlerts, summarizeHealth } from "../lib/alerts";
import { useFreshness } from "../lib/inventory";
import Menu, { MenuItem } from "../components/Menu";
import CreateVmWizard from "../wizard/CreateVmWizard";
import CreateContainerWizard from "../wizard/CreateContainerWizard";
import AccountSecurityModal from "../../components/AccountSecurityModal";
import ChangePasswordDrawer from "../components/ChangePasswordDrawer";
import PreferencesDrawer from "../components/PreferencesDrawer";
import EnclaveMark from "../../components/EnclaveMark";
import { useEnvironmentLabel } from "../lib/environment";
import NotificationsPanel from "./NotificationsPanel";
import { useFeed } from "../lib/feed";
import { usePolling } from "../lib/polling";

// The operations running now: the cluster's tasks, plus the ones this tab started that the server has not listed yet.
export function useRunningOps() {
  const running = useFreshness((s) => s.running);
  const local = useInfraStore((s) => s.tasks);
  return useMemo(() => {
    const seen = new Set(running.map((r) => `${r.type}:${r.target}`));
    const mine = local.filter((x) => x.status === "running" && !seen.has(`${x.type}:${x.target}`));
    return [...running, ...mine];
  }, [running, local]);
}

// The open alerts of the inventory (stopped nodes, crashed VMs, full pools, failed tasks...).
export function useAlerts() {
  const { nodes, vms, storagePools, tasks } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, tasks: s.tasks })));
  return useMemo(() => deriveAlerts({ nodes, vms, storagePools, tasks }), [nodes, vms, storagePools, tasks]);
}

// The account: who is signed in and on which cluster, then language, theme, account settings and sign-out.
function AccountMenu({ dialog, setDialog }) {
  const t = useT();
  const { role, username, logout, authSource } = useAuthStore(useShallow((s) => ({ role: s.role, username: s.username, logout: s.logout, authSource: s.authSource })));
  const caps = capabilities(role);
  const cluster = useFreshness((s) => s.cluster);
  const lang = useLangStore((s) => s.lang);
  const setLang = useLangStore((s) => s.setLang);
  const { mode, setMode } = useThemeStore(useShallow((s) => ({ mode: s.mode, setMode: s.setMode })));
  const [menu, setMenu] = useState(false);
  const btn = useRef(null);
  const open = (d) => { setMenu(false); setDialog(d); };
  const place = cluster?.in_cluster ? cluster.name : t("crumb.datacenter");
  return (
    <span className="nx-relative">
      <button ref={btn} type="button" className="hc-acct nx-rail-user" aria-haspopup="menu" aria-expanded={menu} title={t("top.user")} onClick={() => setMenu((o) => !o)}>
        <span className="hc-acct-t"><b>{username}</b><small>{place}</small></span>
        {/* The initials are drawn by the style sheet: the button's name stays what it shows, the user and the place. */}
        <span className="hc-avatar" aria-hidden="true" data-initials={(username || "?").slice(0, 2).toUpperCase()} />
      </button>
      <Menu open={menu} onClose={() => setMenu(false)} label={username} returnFocusRef={btn} style={{ top: "calc(100% + 6px)", right: 0, minWidth: "15rem" }}>
        <div className="nx-menu-label">{username} · {caps.admin ? t("top.role.admin") : t("top.role.observer")}</div>
        <hr />
        <div className="nx-menu-label">{t("top.language")}</div>
        {LANGS.map((l) => <MenuItem key={l.id} onSelect={() => setLang(l.id)}>{l.label}{lang === l.id ? " ✓" : ""}</MenuItem>)}
        <hr />
        <div className="nx-menu-label">{t("top.theme")}</div>
        {["system", "light", "dark"].map((m) => <MenuItem key={m} onSelect={() => setMode(m)}>{t(`top.theme.${m}`)}{mode === m ? " ✓" : ""}</MenuItem>)}
        <hr />
        {caps.admin && <MenuItem onSelect={() => { setMenu(false); useInfraStore.getState().navigateTo("datacenter", null, "updates"); }}>{t("top.updates")}</MenuItem>}
        {authSource !== "sso" && authSource !== "ldap" && <MenuItem onSelect={() => open("password")}>{t("pw.changeMenu")}</MenuItem>}
        <MenuItem onSelect={() => open("security")}>{t("top.security")}</MenuItem>
        <MenuItem onSelect={() => open("prefs")}>{t("prefs.menu")}</MenuItem>
        <hr />
        <MenuItem danger onSelect={logout}>{t("top.signout")}</MenuItem>
      </Menu>
      <AccountSecurityModal open={dialog === "security"} onClose={() => setDialog(null)} triggerRef={btn} />
      <ChangePasswordDrawer open={dialog === "password"} onClose={() => setDialog(null)} />
      <PreferencesDrawer open={dialog === "prefs"} onClose={() => setDialog(null)} />
    </span>
  );
}

// The header across the top: the menu button, the product, the environment label, the search, the notifications
// (running operations and alerts), the preferences and the account.
export default function TopBar({ onOpenPalette, onToggleNavigator, wizards, setWizards }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const [panel, setPanel] = useState(false);
  const [dialog, setDialog] = useState(null); // update | security | password | prefs
  const bellBtn = useRef(null);
  const menuBtn = useRef(null);
  const env = useEnvironmentLabel();
  const ops = useRunningOps();
  const alerts = useAlerts();
  const health = summarizeHealth(alerts);
  const { unread, fresh, load: loadFeed } = useFeed();
  usePolling(loadFeed, 30000);
  useEffect(() => { loadFeed(); }, [loadFeed]);
  // Each new entry once, as a toast: the bell keeps it until it is read.
  useEffect(() => {
    for (const e of fresh.slice(0, 3)) {
      const k = `nf.${e.kind}`; const v = t(k, { subject: e.subject || "" });
      useInfraStore.getState().pushToast({ kind: e.level === "error" ? "error" : e.level === "warning" ? "warning" : "info", title: v === k ? e.title : v, message: e.detail || "" });
    }
  }, [fresh, t]);
  // The bell counts what is unread and what is critical now, not the routine operations.
  const count = unread + (health.critical ? 1 : 0);

  return (
    <header className="hc-top">
      <button ref={menuBtn} type="button" className="hc-hb" aria-label={t("nav.toggleSidebar")} onClick={onToggleNavigator}>
        <MenuIcon size={20} aria-hidden="true" />
      </button>
      <button type="button" className="hc-brand" onClick={() => navigateTo("datacenter", null, "summary")} aria-label={`${t("app.name")} — ${t("nav.overview")}`}>
        <EnclaveMark size={24} />
        <span>{t("app.name")}</span>
      </button>
      {env && <span className="hc-env" title={t("env.title", { label: env })}>{env}</span>}
      <div className="hc-find">
        <button type="button" className="hc-find-box" onClick={onOpenPalette} aria-label={t("find.open")}>
          <Search size={16} aria-hidden="true" />
          <span className="hc-find-lb">{t("find.placeholder")}</span>
          <span className="hc-kbd" aria-hidden="true">/</span>
        </button>
      </div>
      <button ref={bellBtn} type="button" className="hc-hb" onClick={() => setPanel(true)} aria-haspopup="dialog" aria-expanded={panel}
        aria-label={`${t("notif.title")} — ${t("nf.unreadLabel", { n: unread })}, ${t("top.opsLabel", { n: ops.length })}, ${t("top.alertsLabel", { n: health.total })}`} title={t("notif.title")}>
        <Bell size={18} aria-hidden="true" />
        {count > 0 && <span className="hc-dot" data-tone={health.critical ? "danger" : undefined}>{count}</span>}
      </button>
      <button type="button" className="hc-hb hc-hb--opt" onClick={() => setDialog("prefs")} aria-label={t("prefs.menu")} title={t("prefs.menu")}>
        <Settings size={18} aria-hidden="true" />
      </button>
      <AccountMenu dialog={dialog} setDialog={setDialog} />

      <NotificationsPanel open={panel} onClose={() => setPanel(false)} ops={ops} alerts={alerts} returnFocusRef={bellBtn} />
      <CreateVmWizard open={!!wizards.vm} onClose={() => setWizards({})} triggerRef={menuBtn} />
      <CreateContainerWizard open={!!wizards.container} onClose={() => setWizards({})} triggerRef={menuBtn} />
    </header>
  );
}
