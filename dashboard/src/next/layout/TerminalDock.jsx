import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Download, Maximize2, Minimize2, Plus, SquareTerminal, X } from "lucide-react";
import { createHostTerminalTicket, createTerminalTicket, downloadFromTerminal, endTerminalSession, fetchTerminal, openTerminalSession, terminalTicket, uploadToTerminal } from "../../api/client";
import { promptText } from "../../store/usePromptStore";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { useAccess } from "../lib/access";
import { useTerminalDock } from "../lib/terminalDock";
import TerminalView from "../components/TerminalView";

// The Terminal's docked panel (docs/design/workbench.md, section 11): at the bottom of every page, kept while moving
// between pages, re-attached after a reload. Tabs: Terminal sessions (the box, with the account's own rights), a
// node's root shell (administrators), a VM's terminal.
export default function TerminalDock() {
  const t = useT();
  const dock = useTerminalDock();
  const admin = !!useAccess((s) => s.me?.admin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [info, setInfo] = useState(null);
  const [menu, setMenu] = useState(false);
  const dragging = useRef(null);
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const id = setInterval(tick, 30000);
    return () => clearInterval(id);
  }, []);

  const refresh = useCallback(() => fetchTerminal().then(setInfo).catch(() => setInfo(null)), []);
  useEffect(() => { refresh(); }, [refresh]);
  // Tabs of sessions that ended elsewhere (idle, time limit, closed by an administrator) go after a reload.
  useEffect(() => {
    if (!info) return;
    const open = new Set(info.sessions.map((s) => s.id));
    for (const tab of useTerminalDock.getState().tabs) if (tab.kind === "workbench" && tab.sid && !open.has(tab.sid)) useTerminalDock.getState().update(tab.id, { ended: t("tm.endedElsewhere") });
  }, [info, t]);

  async function newTerminal() {
    setMenu(false);
    const id = dock.add({ kind: "workbench", title: t("tm.tab"), sid: null });
    try {
      const s = await openTerminalSession();
      useTerminalDock.getState().update(id, { sid: s.id, openedAt: s.opened_at });
      refresh();
    } catch (e) { useTerminalDock.getState().update(id, { ended: errorMessage(e) }); }
  }
  function newRoot() { setMenu(false); dock.add({ kind: "root", title: t("tm.root") }); }
  async function closeTab(tab) {
    if (tab.kind === "workbench" && tab.sid && !tab.ended) {
      if (!(await confirmAction({ title: t("tm.endTitle"), message: t("tm.endMsg"), confirmLabel: t("tm.end"), danger: true }))) return;
      try { await endTerminalSession(tab.sid); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
      refresh();
    }
    dock.remove(tab.id);
  }
  // Files dropped onto a Terminal tab go to the session's home, under their own name.
  async function onDrop(e, tab) {
    e.preventDefault();
    if (tab.kind !== "workbench" || !tab.sid || tab.ended) return;
    for (const file of [...(e.dataTransfer?.files || [])]) {
      try { await uploadToTerminal(tab.sid, file.name, file); pushToast({ kind: "success", title: t("tm.uploaded", { name: file.name }) }); } catch (err) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(err) }); }
    }
  }
  async function download(tab) {
    const path = await promptText({ title: t("tm.download"), label: t("tm.downloadPath"), defaultValue: "" });
    if (!path?.trim()) return;
    try {
      const blob = await downloadFromTerminal(tab.sid, path.trim());
      const url = URL.createObjectURL(blob);
      Object.assign(document.createElement("a"), { href: url, download: path.trim().split("/").pop() }).click();
      URL.revokeObjectURL(url);
    } catch (err) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(err) }); }
  }
  function onDrag(e) {
    e.preventDefault();
    dragging.current = { y: e.clientY, h: dock.height };
    const move = (ev) => dock.setHeight(dragging.current.h + (dragging.current.y - ev.clientY));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // The page underneath keeps its end reachable above the panel.
  const shown = dock.open && dock.tabs.length > 0;
  useEffect(() => {
    document.documentElement.style.setProperty("--hc-dock-h", shown && !dock.maximized ? `${dock.height}px` : "0px");
    return () => document.documentElement.style.setProperty("--hc-dock-h", "0px");
  }, [shown, dock.height, dock.maximized]);
  if (!shown) return null;
  const active = dock.tabs.find((x) => x.id === dock.active) || dock.tabs[0];
  const limits = info?.limits;
  const left = active.kind === "workbench" && active.openedAt && limits && now
    ? Math.max(0, Math.round((new Date(active.openedAt).getTime() + limits.max_hours * 3600e3 - now) / 60000)) : null;
  return (
    <section className={`hc-dock${dock.maximized ? " is-max" : ""}`} style={dock.maximized ? undefined : { height: dock.height }} aria-label={t("tm.panel")}>
      <div className="hc-dock-grip" onPointerDown={onDrag} role="separator" aria-orientation="horizontal" aria-label={t("tm.resize")} tabIndex={0}
        onKeyDown={(e) => { if (e.key === "ArrowUp") dock.setHeight(dock.height + 40); if (e.key === "ArrowDown") dock.setHeight(dock.height - 40); }} />
      <div className="hc-dock-bar">
        <div className="hc-dock-tabs" role="tablist" aria-label={t("tm.panel")}>
          {dock.tabs.map((tab, i) => (
            <div key={tab.id} className={`hc-dock-tab${tab.id === active.id ? " is-on" : ""}`}>
              <button type="button" role="tab" aria-selected={tab.id === active.id} onClick={() => dock.activate(tab.id)}>
                <SquareTerminal size={13} aria-hidden="true" /> {tab.title}{tab.kind === "workbench" ? ` ${i + 1}` : ""}
              </button>
              <button type="button" className="hc-dock-x" aria-label={t("tm.closeX", { tab: tab.title })} onClick={() => closeTab(tab)}><X size={12} aria-hidden="true" /></button>
            </div>
          ))}
          <div className="hc-dock-add">
            <button type="button" className="hc-hb" aria-label={t("tm.new")} aria-haspopup="menu" aria-expanded={menu} onClick={() => (admin ? setMenu(!menu) : newTerminal())}><Plus size={15} aria-hidden="true" /></button>
            {menu && (
              <div className="hc-dock-menu" role="menu">
                <button type="button" role="menuitem" onClick={newTerminal}>{t("tm.newTerminal")}</button>
                <button type="button" role="menuitem" onClick={newRoot}>{t("tm.newRoot")}</button>
              </div>
            )}
          </div>
        </div>
        <span className="hc-dock-info">
          {active.kind === "workbench" && !active.ended && t("tm.asYou")}
          {active.kind === "root" && t("tm.rootWarn")}
          {left != null && ` · ${t("tm.left", { n: left })}`}
          {info?.recording && active.kind === "workbench" && ` · ${t("tm.recorded")}`}
        </span>
        {active.kind === "workbench" && active.sid && !active.ended && <button type="button" className="hc-hb" aria-label={t("tm.download")} title={`${t("tm.download")} · ${t("tm.dropHint")}`} onClick={() => download(active)}><Download size={15} aria-hidden="true" /></button>}
        <button type="button" className="hc-hb" aria-label={dock.maximized ? t("tm.restore") : t("tm.maximize")} onClick={dock.toggleMax}>{dock.maximized ? <Minimize2 size={15} aria-hidden="true" /> : <Maximize2 size={15} aria-hidden="true" />}</button>
        <button type="button" className="hc-hb" aria-label={t("tm.hide")} onClick={dock.hide}><ChevronDown size={16} aria-hidden="true" /></button>
      </div>
      <div className="hc-dock-body" onDragOver={(e) => e.preventDefault()} onDrop={(e) => onDrop(e, active)}>
        {dock.tabs.map((tab) => <DockTab key={tab.id} tab={tab} visible={tab.id === active.id} />)}
      </div>
    </section>
  );
}

function DockTab({ tab, visible }) {
  const t = useT();
  const getUrl = useCallback(async () => {
    if (tab.kind === "root") return `/host/terminal?ticket=${encodeURIComponent((await createHostTerminalTicket()).ticket)}`;
    if (tab.kind === "ssh") return `/vms/${encodeURIComponent(tab.vm)}/terminal?ticket=${encodeURIComponent((await createTerminalTicket(tab.vm, tab.node)).ticket)}${tab.node && tab.node !== "local" ? `&node=${encodeURIComponent(tab.node)}` : ""}`;
    return `/workbench/sessions/${tab.sid}/attach?ticket=${encodeURIComponent((await terminalTicket(tab.sid)).ticket)}`;
  }, [tab.kind, tab.sid, tab.vm, tab.node]);
  const onEnded = useCallback((why) => useTerminalDock.getState().update(tab.id, { ended: why }), [tab.id]);
  if (tab.ended) return <div className="hc-tv" hidden={!visible}><p className="hc-tv-state">{t("tm.endedWhy", { why: tab.ended })}</p></div>;
  if (tab.kind === "workbench" && !tab.sid) return <div className="hc-tv" hidden={!visible}><p className="hc-tv-state">{t("nn.connecting")}</p></div>;
  return <TerminalView getUrl={getUrl} visible={visible} label={tab.title} onEnded={onEnded} />;
}

// The top bar's button: opens the panel, with a first Terminal when it has none. Hidden when the Terminal is not
// open to this account (an administrator still gets the panel for the root shell).
export function TerminalButton() {
  const t = useT();
  const [allowed, setAllowed] = useState(null);
  useEffect(() => { fetchTerminal().then((x) => setAllowed(x.allowed)).catch(() => setAllowed(false)); }, []);
  const open = useCallback(() => {
    const dock = useTerminalDock.getState();
    if (dock.tabs.length === 0) {
      const id = dock.add({ kind: "workbench", title: t("tm.tab"), sid: null });
      openTerminalSession().then((s) => dock.update(id, { sid: s.id, openedAt: s.opened_at })).catch((e) => dock.update(id, { ended: errorMessage(e) }));
    } else dock.toggle();
  }, [t]);
  useEffect(() => {
    if (!allowed) return undefined;
    const onKey = (e) => { if (e.ctrlKey && (e.key === "`" || e.code === "Backquote")) { e.preventDefault(); open(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [allowed, open]);
  if (!allowed) return null;
  return (
    <button type="button" className="hc-hb" onClick={open} aria-label={t("tm.open")} title={`${t("tm.open")} (Ctrl+\`)`}>
      <SquareTerminal size={18} aria-hidden="true" />
    </button>
  );
}
