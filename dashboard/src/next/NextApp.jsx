import { useCallback, useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import "./next.css";
import "./refonte.css";
import "./shell.css";
import "./console.css";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import ConfirmHost from "../components/ConfirmHost";
import MaintenanceHost from "./components/MaintenanceDialog";
import NodePowerHost from "./components/NodePowerDialog";
import { useT, useLangStore } from "./i18n";
import { useThemeStore } from "./tokens/theme";
import { usePolling } from "./lib/polling";
import { refreshInventory, refreshExtras } from "./lib/inventory";
import TopBar from "./layout/TopBar";
import Workspace from "./layout/Workspace";
import Palette from "./layout/Palette";
import SideMenu from "./layout/SideMenu";
import Tray from "./layout/Tray";
import ShortcutsDialog from "./components/ShortcutsDialog";
import { useInfraStore } from "../store/useInfraStore";
import { GO, SEQUENCE_MS, isTyping, moveRow, toggleRow } from "./lib/shortcuts";

const SIDEBAR_KEY = "hyperlite-next-sidebar";
function readSidebarCollapsed() { try { return localStorage.getItem(SIDEBAR_KEY) === "1"; } catch { return false; } }

const REFRESH_MS = 6000;
const EXTRAS_MS = 15000;

// Root of the rebuilt interface. Same auth, stores, API client and routes as the legacy UI (this
// component is rendered by App.jsx once the session is authenticated).
export default function NextApp() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const initTheme = useThemeStore((s) => s.init);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [wizards, setWizards] = useState({});
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.ui = "next";
    root.lang = lang;
    const cleanup = initTheme();
    return () => {
      cleanup?.();
      delete root.dataset.ui; delete root.dataset.theme;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);

  // The task dock starts collapsed (calm by default); it shows a running-task count and can be opened at any time.

  useEffect(() => { refreshInventory({ initial: true }).catch(() => {}); refreshExtras(); }, []);
  usePolling(useCallback(() => refreshInventory(), []), REFRESH_MS);
  usePolling(useCallback(() => refreshExtras(), []), EXTRAS_MS);

  const toggleSidebar = useCallback(() => {
    if (window.innerWidth < 1024) { setSidebarOpen((o) => !o); return; }
    setSidebarCollapsed((c) => { const next = !c; try { localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0"); } catch { /* preference only */ } return next; });
  }, []);

  useEffect(() => {
    let pendingG = 0; // when "g" was pressed: the next letter goes to a service
    const onKey = (e) => {
      const typing = isTyping(e.target);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaletteOpen(true); return; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") { e.preventDefault(); toggleSidebar(); return; }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
      if (document.querySelector('[aria-modal="true"]')) return; // a dialog has the keyboard
      const key = e.key;
      if (pendingG && Date.now() - pendingG < SEQUENCE_MS) {
        pendingG = 0;
        const id = GO[key.toLowerCase()];
        if (id) { e.preventDefault(); useInfraStore.getState().navigateTo("datacenter", null, id); }
        return;
      }
      pendingG = 0;
      if (key === "/") { e.preventDefault(); setPaletteOpen(true); }
      else if (key === "?") { e.preventDefault(); setShortcutsOpen(true); }
      else if (key === "g") { pendingG = Date.now(); }
      else if (key === "c") { e.preventDefault(); useInfraStore.getState().navigateTo("datacenter", null, "create"); }
      else if (key === "j" || key === "k") { if (moveRow(key === "j" ? 1 : -1)) e.preventDefault(); }
      else if (key === "x") { if (toggleRow()) e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleSidebar]);
  useEffect(() => {
    const open = () => setShortcutsOpen(true);
    window.addEventListener("nx:shortcuts", open);
    return () => window.removeEventListener("nx:shortcuts", open);
  }, []);

  useEffect(() => {
    const open = () => setPaletteOpen(true);
    window.addEventListener("nx:palette", open);
    return () => window.removeEventListener("nx:palette", open);
  }, []);

  useEffect(() => {
    const on = (e) => setWizards({ [e.detail]: true });
    window.addEventListener("nx:wizard", on);
    return () => window.removeEventListener("nx:wizard", on);
  }, []);

  useEffect(() => {
    const close = () => { if (window.innerWidth < 1024) setSidebarOpen(false); };
    window.addEventListener("nx:navigated", close);
    return () => window.removeEventListener("nx:navigated", close);
  }, []);

  // Escape closes the narrow-screen drawer.
  useEffect(() => {
    if (!sidebarOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setSidebarOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  const sidebarState = sidebarOpen ? "open" : sidebarCollapsed ? "collapsed" : undefined;

  return (
    <TooltipProvider delayDuration={300}>
      <div className="nx-root hc" data-sidebar={sidebarState}>
        <a className="nx-skip" href="#nx-main" onClick={(e) => { e.preventDefault(); document.getElementById("nx-main")?.focus(); }}>{t("skip")}</a>
        {sidebarOpen && <div className="nx-scrim nx-scrim--nav" onClick={() => setSidebarOpen(false)} />}
        <TopBar onOpenPalette={() => setPaletteOpen(true)} onToggleNavigator={toggleSidebar} wizards={wizards} setWizards={setWizards} />
        <SideMenu collapsed={sidebarCollapsed && !sidebarOpen} drawerOpen={sidebarOpen} onToggleCollapse={toggleSidebar} />
        <Routes>
          <Route path="/" element={<Navigate to="/datacenter" replace />} />
          <Route path="/datacenter" element={<Workspace />} />
          <Route path="/node/:id" element={<Workspace />} />
          <Route path="/vm/:id" element={<Workspace />} />
          <Route path="/container/:id" element={<Workspace />} />
          <Route path="*" element={<Navigate to="/datacenter" replace />} />
        </Routes>
        <Tray />
        <Palette open={paletteOpen} onClose={() => setPaletteOpen(false)} setWizards={setWizards} />
        <ShortcutsDialog open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
        <MaintenanceHost />
        <NodePowerHost />
      </div>
      <Toaster position="bottom-right" closeButton />
      <ConfirmHost confirmLabel={t("action.confirm")} cancelLabel={t("action.cancel")} />
    </TooltipProvider>
  );
}
