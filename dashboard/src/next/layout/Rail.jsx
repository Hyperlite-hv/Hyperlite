import { useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Activity, Database, LayoutGrid, Network, Settings, Shield, Zap } from "lucide-react";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore, LANGS } from "../i18n";
import { useThemeStore } from "../tokens/theme";
import { capabilities } from "../lib/capabilities";
import { SECTIONS } from "../lib/sections";
import Menu, { MenuItem } from "../components/Menu";
import UpdateDialog from "../components/UpdateDialog";
import AccountSecurityModal from "../../components/AccountSecurityModal";
import ChangePasswordDrawer from "../components/ChangePasswordDrawer";
import PreferencesDrawer from "../components/PreferencesDrawer";
import EnclaveMark from "../../components/EnclaveMark";

const ICONS = { inventory: LayoutGrid, storage: Database, network: Network, protection: Shield, monitoring: Activity, automation: Zap, admin: Settings };

function RailButton({ id, label, active, onClick }) {
  const I = ICONS[id];
  return (
    <button type="button" className={`nx-rail-btn${active ? " is-on" : ""}`} aria-label={label} title={label} aria-current={active ? "page" : undefined} onClick={onClick}>
      <I size={20} strokeWidth={1.75} aria-hidden="true" />
    </button>
  );
}

// The dark column on the left: the product mark, one button per section, then administration and the account menu.
export default function Rail({ section, onSection }) {
  const t = useT();
  const { role, username, logout, authSource } = useAuthStore(useShallow((s) => ({ role: s.role, username: s.username, logout: s.logout, authSource: s.authSource })));
  const caps = capabilities(role);
  const lang = useLangStore((s) => s.lang);
  const setLang = useLangStore((s) => s.setLang);
  const { mode, setMode } = useThemeStore(useShallow((s) => ({ mode: s.mode, setMode: s.setMode })));
  const [menu, setMenu] = useState(false);
  const [dialog, setDialog] = useState(null); // update | security | password | prefs
  const userBtn = useRef(null);
  const open = (d) => { setMenu(false); setDialog(d); };
  const main = SECTIONS.filter((s) => !s.admin);
  const admin = SECTIONS.find((s) => s.admin);

  return (
    <nav className="nx-rail" aria-label={t("nav.sections")}>
      <span className="nx-rail-mark" title={t("app.name")}><EnclaveMark size={26} rails="var(--color-text-primary)" core="var(--color-accent)" /></span>
      {main.map((s) => <RailButton key={s.id} id={s.id} label={t(s.label)} active={section === s.id} onClick={() => onSection(s.id)} />)}
      <span className="nx-rail-gap" />
      {caps.admin && <RailButton id={admin.id} label={t(admin.label)} active={section === admin.id} onClick={() => onSection(admin.id)} />}
      <span className="nx-relative">
        <button ref={userBtn} type="button" className="nx-rail-user" aria-label={`${t("top.user")} — ${username}`} title={username} aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((o) => !o)}>
          {(username || "?").slice(0, 2).toUpperCase()}
        </button>
        <Menu open={menu} onClose={() => setMenu(false)} label={username} returnFocusRef={userBtn} style={{ bottom: 0, left: "calc(100% + 8px)", minWidth: "15rem" }}>
          <div className="nx-menu-label">{username} · {caps.admin ? t("top.role.admin") : t("top.role.observer")}</div>
          <hr />
          <div className="nx-menu-label">{t("top.language")}</div>
          {LANGS.map((l) => <MenuItem key={l.id} onSelect={() => setLang(l.id)}>{l.label}{lang === l.id ? " ✓" : ""}</MenuItem>)}
          <hr />
          <div className="nx-menu-label">{t("top.theme")}</div>
          {["light", "dark", "system"].map((m) => <MenuItem key={m} onSelect={() => setMode(m)}>{t(`top.theme.${m}`)}{mode === m ? " ✓" : ""}</MenuItem>)}
          <hr />
          {caps.admin && <MenuItem onSelect={() => open("update")}>{t("top.updates")}</MenuItem>}
          {authSource !== "sso" && authSource !== "ldap" && <MenuItem onSelect={() => open("password")}>{t("pw.changeMenu")}</MenuItem>}
          <MenuItem onSelect={() => open("security")}>{t("top.security")}</MenuItem>
          <MenuItem onSelect={() => open("prefs")}>{t("prefs.menu")}</MenuItem>
          <hr />
          <MenuItem danger onSelect={logout}>{t("top.signout")}</MenuItem>
        </Menu>
      </span>
      {dialog === "update" && <UpdateDialog onClose={() => setDialog(null)} />}
      <AccountSecurityModal open={dialog === "security"} onClose={() => setDialog(null)} triggerRef={userBtn} />
      <ChangePasswordDrawer open={dialog === "password"} onClose={() => setDialog(null)} />
      <PreferencesDrawer open={dialog === "prefs"} onClose={() => setDialog(null)} />
    </nav>
  );
}
