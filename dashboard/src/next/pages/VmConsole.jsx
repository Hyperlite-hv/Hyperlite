import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Camera, Check, ClipboardPaste, Copy, Ellipsis, ExternalLink, Info, Keyboard, Maximize, Minimize, Monitor, MonitorCog, MonitorSmartphone, Play, RotateCw, SquareTerminal, TriangleAlert, Unplug, X } from "lucide-react";
import { addSpiceDisplay, createConsoleTicket, createRdpTicket, createSerialTicket, createTerminalTicket, downloadSpiceFile, fetchRdpAvailable, startVM } from "../../api/client";
import { ensureXtermLoaded, keepFitted, loadTerminalFont, wsUrl } from "../../utils/loadXterm";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { termOptions } from "../lib/prefs";
import { consoleUrl } from "../lib/vmActions";
import { apiNode, vmKey } from "../lib/vmId";
import { localizeApiText } from "../lib/apiText";
import { KEY_COMBOS, MAX_TYPED, sendCombo, textToKeysyms } from "../lib/typeText";
import Menu, { MenuItem } from "../components/Menu";
import WorkstationAccess from "../components/WorkstationAccess";
import { Field } from "../components/ui";

// The console of a VM (docs/design/vm-console.md): one toolbar on one row, the screen in all the room left, its states
// drawn on the stage. Graphical (VNC through noVNC), serial and SSH (xterm), and the remote desktop (the node speaks
// RDP to the guest and shows it as VNC, docs/design/console-2.md section 8). Used by the VM's Console tab and, with
// `standalone`, by the console window.
const RETRY_S = 5;
// About a minute of automatic retries; a refusal (a 4xx) stops at once. Each attempt asks for a ticket, which the server
// journals: an endless loop filled the journal for as long as a tab stayed open.
const MAX_ATTEMPTS = 12;
const MODES = [
  { id: "vnc", icon: Monitor, label: "vc.vnc" },
  { id: "serial", icon: SquareTerminal, label: "vc.serial" },
  { id: "terminal", icon: SquareTerminal, label: "vc.ssh" },
  { id: "rdp", icon: MonitorSmartphone, label: "rdp.button" },
];
const HINT_KEY = "hl.console.hintClosed";
const readClosed = () => { try { return JSON.parse(localStorage.getItem(HINT_KEY)) || {}; } catch { return {}; } };
const SCALES = ["fit", "real", "remote"];

// The stage's height: what the window has below it, so the screen never runs off a laptop nor leaves a big screen empty.
function useStageHeight(ref, standalone, deps) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || standalone) return undefined;
    const size = () => {
      if (document.fullscreenElement) { el.style.height = ""; return; }
      const top = el.getBoundingClientRect().top + window.scrollY;
      el.style.height = `${Math.max(320, window.innerHeight - top - 16)}px`;
    };
    size();
    window.addEventListener("resize", size);
    document.addEventListener("fullscreenchange", size);
    return () => { window.removeEventListener("resize", size); document.removeEventListener("fullscreenchange", size); };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
}

// A toolbar button: an icon with its name as tooltip and for screen readers; `menu` opens one, `toggle` is pressed or not.
function Tool({ icon: Icon, label, onClick, toolRef, menu, open, toggle, pressed, disabled, reason }) {
  return (
    <button ref={toolRef} type="button" className="hc-cs-tool" aria-label={label} title={disabled && reason ? `${label} — ${reason}` : label} disabled={disabled}
      aria-haspopup={menu ? "menu" : undefined} aria-expanded={menu ? !!open : undefined} aria-pressed={toggle ? !!pressed : undefined} onClick={onClick}>
      <Icon size={16} aria-hidden="true" />
    </button>
  );
}

export default function VmConsole({ resource: vm, standalone = false, initialMode = "vnc" }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const name = vm?.name;
  const node = apiNode(vm?.node);
  const running = vm?.state === "active" || vm?.state === "running";
  const [mode, setMode] = useState(MODES.some((m) => m.id === initialMode) ? initialMode : "vnc");
  const [status, setStatus] = useState("idle"); // idle, connecting, connected, error
  const [error, setError] = useState(null);
  const [retryIn, setRetryIn] = useState(null);
  const [gaveUp, setGaveUp] = useState(false);
  const [size, setSize] = useState(null); // the guest's resolution, graphical modes
  const [scale, setScale] = useState("fit");
  const [focused, setFocused] = useState(false);
  const [isFull, setIsFull] = useState(false);
  const [menu, setMenu] = useState(null); // keys, display, more
  const [clipOpen, setClipOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busyTyping, setBusyTyping] = useState(false);
  const [guestClip, setGuestClip] = useState(null);
  const [note, setNote] = useState(null); // the outcome of a SPICE action: the console's own window shows no toasts
  const [wsOpen, setWsOpen] = useState(false);
  const [closedHints, setClosedHints] = useState(readClosed);
  const [rdpForm, setRdpForm] = useState({ username: "", password: "", domain: "" });
  const [rdpAvail, setRdpAvail] = useState(null);
  const [rdpGo, setRdpGo] = useState(0); // bumped by the sign-in form: the remote desktop never connects by itself
  const attempts = useRef(0);
  const frame = useRef(null);
  const stage = useRef(null);
  const screen = useRef(null);
  const rfb = useRef(null);
  const term = useRef(null);
  const ws = useRef(null);
  const onResize = useRef(null);
  const keysBtn = useRef(null);
  const displayBtn = useRef(null);
  const moreBtn = useRef(null);
  const text = mode === "serial" || mode === "terminal";
  const graphical = !text;
  const connected = status === "connected";
  const sshRefused = mode === "terminal" && !caps.admin;

  useStageHeight(stage, standalone, [mode, standalone, closedHints, clipOpen, note]);

  const cleanup = useCallback(() => {
    try { rfb.current?.disconnect(); } catch { /* already closed */ }
    try { ws.current?.close(); } catch { /* already closed */ }
    try { term.current?.dispose(); } catch { /* already disposed */ }
    onResize.current?.();
    rfb.current = null; ws.current = null; term.current = null; onResize.current = null;
    if (screen.current) screen.current.innerHTML = "";
    setStatus("idle"); setSize(null);
  }, []);
  const failed = useCallback((e) => {
    setError(errorMessage(e)); setStatus("error");
    if (e?.status >= 400 && e.status < 500) setGaveUp(true); // a refusal does not change by asking again
  }, []);

  // Graphical modes: noVNC on the console relay (VNC) or on the remote desktop's session (RDP).
  const connectRfb = useCallback(async (kind) => {
    setStatus("connecting"); setError(null);
    try {
      let path;
      if (kind === "rdp") {
        const box = stage.current?.getBoundingClientRect();
        const ticket = await createRdpTicket(name, node, { ...rdpForm, width: Math.round(box?.width || 1280), height: Math.round(box?.height || 800) });
        setRdpForm((f) => ({ ...f, password: "" })); // used once, not kept
        path = `/vms/${encodeURIComponent(name)}/rdp?ticket=${encodeURIComponent(ticket.ticket)}`;
      } else {
        const ticket = await createConsoleTicket(name, node);
        path = `/vms/${encodeURIComponent(name)}/console?ticket=${encodeURIComponent(ticket.ticket)}`;
      }
      // noVNC lives in public/novnc, outside the bundle: loaded as is at runtime.
      const mod = await import(/* @vite-ignore */ new URL("/novnc/core/rfb.js", window.location.origin).href);
      if (!screen.current) return;
      screen.current.innerHTML = "";
      const r = new mod.default(screen.current, wsUrl(path));
      r.background = "transparent";
      r.focusOnClick = true;
      r.scaleViewport = scale !== "real";
      r.clipViewport = scale === "real";
      r.resizeSession = scale === "remote" && kind === "vnc";
      r.qualityLevel = 7;
      r.compressionLevel = 2;
      rfb.current = r;
      let reason = "";
      try { r._sock?._websocket?.addEventListener?.("close", (ev) => { reason = ev.reason || ""; }); } catch { /* noVNC's socket not reachable: no reason shown */ }
      const readSize = () => { if (r._fbWidth) setSize({ w: r._fbWidth, h: r._fbHeight }); };
      r.addEventListener("connect", () => { attempts.current = 0; setStatus("connected"); readSize(); setTimeout(readSize, 1500); });
      r.addEventListener("disconnect", (ev) => {
        rfb.current = null; setSize(null);
        if (kind === "rdp") { setError(reason || (ev.detail?.clean ? null : t("rdp.dropped"))); setGaveUp(true); setStatus("error"); return; }
        if (!ev.detail?.clean) setError(reason || t("cs.dropped"));
        setStatus(ev.detail?.clean ? "idle" : "error");
      });
      r.addEventListener("credentialsrequired", () => { setError(t("vc.credentials")); setStatus("error"); setGaveUp(true); });
      // A guest with a clipboard agent sends what it copies: offered in the clipboard panel.
      r.addEventListener("clipboard", (e) => setGuestClip(e.detail?.text || null));
      r.addEventListener("desktopname", readSize);
    } catch (e) { failed(e); }
  }, [name, node, rdpForm, scale, t, failed]);

  const connectTerminal = useCallback(async () => {
    setStatus("connecting"); setError(null);
    try {
      await ensureXtermLoaded();
      const serial = mode === "serial";
      const ticket = await (serial ? createSerialTicket : createTerminalTicket)(name, node);
      if (!screen.current) return;
      screen.current.innerHTML = "";
      const options = termOptions();
      await loadTerminalFont(options);
      // eslint-disable-next-line no-undef
      const tm = new Terminal(options);
      // eslint-disable-next-line no-undef
      const fit = new FitAddon.FitAddon();
      tm.loadAddon(fit); tm.open(screen.current); fit.fit(); term.current = tm;
      const sock = new WebSocket(wsUrl(`/vms/${encodeURIComponent(name)}/${serial ? "serial" : "terminal"}?ticket=${encodeURIComponent(ticket.ticket)}`));
      ws.current = sock;
      sock.onopen = () => { attempts.current = 0; setStatus("connected"); fit.fit(); sock.send("\x00" + JSON.stringify({ cols: tm.cols, rows: tm.rows })); tm.focus(); };
      sock.onmessage = (ev) => tm.write(ev.data);
      sock.onclose = (ev) => { if (ws.current !== sock) return; ws.current = null; setError(ev.reason || t("cs.dropped")); setStatus("error"); };
      tm.onData((d) => { if (sock.readyState === WebSocket.OPEN) sock.send(d); });
      tm.onResize(({ cols, rows }) => { if (sock.readyState === WebSocket.OPEN) sock.send("\x00" + JSON.stringify({ cols, rows })); });
      onResize.current = keepFitted(screen.current, fit);
    } catch (e) { failed(e); }
  }, [mode, name, node, t, failed]);

  // A change of VM or mode ends the session; leaving the page too.
  useEffect(() => cleanup, [cleanup, name, node, mode]);
  useEffect(() => { attempts.current = 0; setGaveUp(false); setError(null); }, [name, node, mode]);
  useEffect(() => { if (mode === "rdp" && rdpAvail == null && name) fetchRdpAvailable(name, node).then(setRdpAvail).catch(() => setRdpAvail({ available: false })); }, [mode, name, node, rdpAvail]);

  // Automatic connection: at once, then every RETRY_S seconds after an error or a drop while the VM runs, up to
  // MAX_ATTEMPTS. The remote desktop waits for its sign-in form.
  const auto = running && !sshRefused && mode !== "rdp";
  useEffect(() => {
    if (!auto || gaveUp || (status !== "idle" && status !== "error")) { setRetryIn(null); return undefined; }
    if (attempts.current >= MAX_ATTEMPTS) { setGaveUp(true); setRetryIn(null); return undefined; }
    const delay = attempts.current === 0 ? 0 : RETRY_S;
    attempts.current += 1;
    setRetryIn(delay || null);
    let left = delay;
    const tick = delay ? setInterval(() => { left -= 1; setRetryIn(left > 0 ? left : null); }, 1000) : null;
    const go = setTimeout(() => { if (tick) clearInterval(tick); setRetryIn(null); if (text) connectTerminal(); else connectRfb("vnc"); }, delay * 1000);
    return () => { clearTimeout(go); if (tick) clearInterval(tick); };
  }, [auto, gaveUp, status, mode, name, node]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (rdpGo && mode === "rdp") connectRfb("rdp"); }, [rdpGo]); // eslint-disable-line react-hooks/exhaustive-deps

  // The display's scale follows the choice without reconnecting.
  useEffect(() => {
    const r = rfb.current;
    if (!r) return;
    r.scaleViewport = scale !== "real";
    r.clipViewport = scale === "real";
    r.resizeSession = scale === "remote" && mode === "vnc";
  }, [scale, mode, status]);
  useEffect(() => {
    const onFull = () => setIsFull(document.fullscreenElement === frame.current);
    document.addEventListener("fullscreenchange", onFull);
    return () => document.removeEventListener("fullscreenchange", onFull);
  }, []);

  function switchMode(m) {
    if (m === mode) return;
    cleanup(); attempts.current = 0; setError(null); setGaveUp(false); setMenu(null); setMode(m);
  }
  const reconnect = () => {
    cleanup(); attempts.current = 0; setError(null); setGaveUp(false); setMenu(null);
    if (mode === "rdp") setStatus("idle");
  };
  const disconnect = () => { setMenu(null); cleanup(); setGaveUp(true); setError(null); };
  async function startVm() {
    try { await startVM(name, node); setNote({ tone: "info", text: t("cs.starting") }); } catch (e) { setNote({ tone: "warning", text: errorMessage(e) }); }
  }
  async function typeIntoVm() {
    const r = rfb.current;
    if (!r || !typed) return;
    setBusyTyping(true);
    try {
      const { default: keysyms } = await import(/* @vite-ignore */ new URL("/novnc/core/input/keysymdef.js", window.location.origin).href);
      try { r.clipboardPasteFrom(typed); } catch { /* no clipboard on this server: typing is enough */ }
      for (const sym of textToKeysyms(typed, keysyms.lookup)) {
        if (rfb.current !== r) break;
        r.sendKey(sym, null);
        await new Promise((res) => setTimeout(res, 8));
      }
      setTyped(""); setClipOpen(false); r.focus();
    } finally { setBusyTyping(false); }
  }
  function screenshot() {
    const canvas = screen.current?.querySelector("canvas");
    if (!canvas) return;
    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
      Object.assign(document.createElement("a"), { href: url, download: `${name}-${stamp}.png` }).click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "image/png");
  }
  const fullscreen = () => (document.fullscreenElement ? document.exitFullscreen?.() : frame.current?.requestFullscreen?.());
  const openWindow = () => window.open(consoleUrl(vm, mode), `hyperlite-console-${vmKey(vm)}-${mode}`, "width=1280,height=860,noopener");
  async function openSpice() {
    setMenu(null);
    try { await downloadSpiceFile(name, node); setNote({ tone: "info", text: t("vc.spiceReadyMsg") }); } catch (e) { setNote({ tone: "warning", text: t("vc.spiceFailed", { detail: errorMessage(e) }) }); }
  }
  async function addSpice() {
    setMenu(null);
    try { const r = await addSpiceDisplay(name, node); setNote({ tone: "info", text: t(r?.effective === "next_start" ? "vc.spiceAddedNext" : "vc.spiceAddedNow") }); } catch (e) { setNote({ tone: "warning", text: t("vc.spiceFailed", { detail: errorMessage(e) }) }); }
  }
  const closeHint = () => { const next = { ...closedHints, [mode]: true }; setClosedHints(next); try { localStorage.setItem(HINT_KEY, JSON.stringify(next)); } catch { /* this page only */ } };

  if (!vm) return null;
  const stateText = connected
    ? (size ? t("cs.connectedSize", { w: size.w, h: size.h }) : t("nn.connected"))
    : status === "connecting" ? t("nn.connecting")
      : retryIn ? t("vc.retryIn", { s: retryIn })
        : !running ? t("cs.vmStopped") : gaveUp || status === "error" ? t("cs.ended") : t("nn.notConnected");
  const tone = connected ? "success" : status === "connecting" || retryIn ? "info" : status === "error" ? "warning" : "offline";
  const hint = mode === "serial" ? t("vc.serialHelp") : mode === "terminal" && caps.admin ? t("vc.sshWarn") : mode === "rdp" ? t("cs.rdpHint") : null;

  // What the stage shows when the screen does not.
  let overlay = null;
  if (!running) {
    overlay = (
      <div className="hc-cs-over">
        <Monitor size={28} aria-hidden="true" />
        <h3>{t("cs.vmStoppedTitle")}</h3>
        <p>{t("cs.vmStoppedText")}</p>
        {caps.power && <button type="button" className="nx-btn nx-btn--primary" onClick={startVm}><Play size={14} aria-hidden="true" /> {t("cs.start")}</button>}
      </div>
    );
  } else if (sshRefused) {
    overlay = <div className="hc-cs-over"><SquareTerminal size={28} aria-hidden="true" /><h3>{t("vc.adminOnlyTitle")}</h3><p>{t("vc.adminOnlyHelp")}</p></div>;
  } else if (mode === "rdp" && status !== "connected" && status !== "connecting") {
    overlay = (
      <form className="hc-cs-over hc-cs-form" onSubmit={(e) => { e.preventDefault(); setError(null); setGaveUp(false); setRdpGo((n) => n + 1); }}>
        <MonitorSmartphone size={28} aria-hidden="true" />
        <h3>{t("rdp.title", { name })}</h3>
        {rdpAvail && !rdpAvail.available && <p className="nx-err" role="alert">{t("rdp.missing", { packages: rdpAvail.packages || "" })}</p>}
        {error && <p className="nx-err" role="alert">{localizeApiText(t, error)}</p>}
        <p className="hc-cs-muted">{t("rdp.note")}</p>
        <Field label={t("rdp.user")}>{(p) => <input {...p} className="nx-inp" autoComplete="username" value={rdpForm.username} onChange={(e) => setRdpForm({ ...rdpForm, username: e.target.value })} required />}</Field>
        <Field label={t("rdp.password")}>{(p) => <input {...p} type="password" className="nx-inp" autoComplete="current-password" value={rdpForm.password} onChange={(e) => setRdpForm({ ...rdpForm, password: e.target.value })} />}</Field>
        <Field label={t("rdp.domain")} hint={t("rdp.domainHint")}>{(p) => <input {...p} className="nx-inp" value={rdpForm.domain} onChange={(e) => setRdpForm({ ...rdpForm, domain: e.target.value })} />}</Field>
        <button type="submit" className="nx-btn nx-btn--primary" disabled={!rdpForm.username || (rdpAvail && !rdpAvail.available)}>{t("rdp.connect")}</button>
      </form>
    );
  } else if (status === "connecting") {
    overlay = <div className="hc-cs-over" role="status"><span className="nx-spin hc-cs-spin" aria-hidden="true" /><p>{t("cs.connectingTo", { name })}</p></div>;
  } else if (!connected) {
    overlay = (
      <div className="hc-cs-over" role={error ? "alert" : "status"}>
        <Unplug size={28} aria-hidden="true" />
        <h3>{retryIn ? t("cs.reconnectingTitle") : t("cs.endedTitle")}</h3>
        {error && <p>{localizeApiText(t, error)}</p>}
        <p className="hc-cs-muted">{retryIn ? t("vc.retryIn", { s: retryIn }) : gaveUp ? t("vc.gaveUp") : ""}</p>
        <button type="button" className="nx-btn nx-btn--primary" onClick={reconnect}><RotateCw size={14} aria-hidden="true" /> {t("cs.reconnect")}</button>
      </div>
    );
  }

  return (
    <div className={`hc-cs${standalone ? " hc-cs--window" : ""}`}>
      <div ref={frame} className="hc-cs-frame" data-full={isFull || undefined}>
        <div className="hc-cs-bar" role="toolbar" aria-label={t("cs.toolbar")}>
          <div className="hc-cs-modes" role="tablist" aria-label={t("vc.mode")}>
            {MODES.map((m) => (
              <button key={m.id} type="button" role="tab" aria-selected={mode === m.id} onClick={() => switchMode(m.id)}>
                <m.icon size={14} aria-hidden="true" /><span>{t(m.label)}</span>
              </button>
            ))}
          </div>
          <span className="hc-cs-state" data-tone={tone} role="status"><span className="nx-dot" data-tone={tone === "info" ? "accent" : tone} aria-hidden="true" />{stateText}</span>
          <span className="nx-sp" />
          <div className="hc-cs-tools">
            {graphical && (
              <span className="nx-relative">
                <Tool icon={Keyboard} label={t("cs.sendKeys")} onClick={() => setMenu(menu === "keys" ? null : "keys")} toolRef={keysBtn} menu open={menu === "keys"} disabled={!connected} reason={t("cs.notConnected")} />
                <Menu open={menu === "keys"} onClose={() => setMenu(null)} label={t("cs.sendKeys")} returnFocusRef={keysBtn} style={{ top: "calc(100% + 4px)", right: 0, minWidth: "17rem" }}>
                  <MenuItem onSelect={() => { setMenu(null); rfb.current?.sendCtrlAltDel(); rfb.current?.focus(); }}>{t("vc.ctrlAltDel")}</MenuItem>
                  {KEY_COMBOS.map((c) => <MenuItem key={c.id} onSelect={() => { setMenu(null); if (rfb.current) { sendCombo(rfb.current, c); rfb.current.focus(); } }}>{t(`vc.key.${c.id}`)}</MenuItem>)}
                </Menu>
              </span>
            )}
            {graphical && <Tool icon={ClipboardPaste} label={t("cs.clipboard")} onClick={() => setClipOpen((o) => !o)} toggle pressed={clipOpen} disabled={!connected} reason={t("cs.notConnected")} />}
            {graphical && (
              <span className="nx-relative">
                <Tool icon={MonitorCog} label={t("cs.display")} onClick={() => setMenu(menu === "display" ? null : "display")} toolRef={displayBtn} menu open={menu === "display"} />
                <Menu open={menu === "display"} onClose={() => setMenu(null)} label={t("cs.display")} returnFocusRef={displayBtn} style={{ top: "calc(100% + 4px)", right: 0, minWidth: "17rem" }}>
                  {SCALES.filter((s) => s !== "remote" || mode === "vnc").map((s) => (
                    <MenuItem key={s} onSelect={() => { setScale(s); setMenu(null); }}><span className="hc-cs-check">{scale === s && <Check size={14} aria-hidden="true" />}</span>{t(`cs.scale.${s}`)}</MenuItem>
                  ))}
                </Menu>
              </span>
            )}
            {graphical && <Tool icon={Camera} label={t("cs.screenshot")} onClick={screenshot} disabled={!connected} reason={t("cs.notConnected")} />}
            <Tool icon={isFull ? Minimize : Maximize} label={isFull ? t("cs.exitFull") : t("vc.fullscreen")} onClick={fullscreen} toggle pressed={isFull} />
            {!standalone && <Tool icon={ExternalLink} label={t("nn.openWindow")} onClick={openWindow} disabled={!running} />}
            <span className="nx-relative">
              <Tool icon={Ellipsis} label={t("cs.more")} onClick={() => setMenu(menu === "more" ? null : "more")} toolRef={moreBtn} menu open={menu === "more"} />
              <Menu open={menu === "more"} onClose={() => setMenu(null)} label={t("cs.more")} returnFocusRef={moreBtn} style={{ top: "calc(100% + 4px)", right: 0, minWidth: "17rem" }}>
                <MenuItem onSelect={reconnect} disabled={!running}>{t("cs.reconnect")}</MenuItem>
                <MenuItem onSelect={disconnect} disabled={!connected && status !== "connecting"}>{t("cs.disconnect")}</MenuItem>
                <MenuItem disabled={!running} reason={t("menu.reason.notRunning")} onSelect={openSpice}>{t("vc.spiceDownload")}</MenuItem>
                <MenuItem disabled={!caps.admin} reason={t("menu.reason.admin")} onSelect={addSpice}>{t("vc.spiceAdd")}</MenuItem>
                <MenuItem onSelect={() => { setMenu(null); setWsOpen(true); }}>{t("ws.button")}</MenuItem>
                {standalone && <MenuItem onSelect={() => window.close()}>{t("vc.closeWindow")}</MenuItem>}
              </Menu>
            </span>
          </div>
        </div>
        {hint && !closedHints[mode] && (
          <div className="hc-cs-hint" data-tone={mode === "terminal" ? "warning" : "info"}>
            {mode === "terminal" ? <TriangleAlert size={14} aria-hidden="true" /> : <Info size={14} aria-hidden="true" />}
            <span>{hint}</span>
            <button type="button" className="hc-cs-x" aria-label={t("action.close")} onClick={closeHint}><X size={14} aria-hidden="true" /></button>
          </div>
        )}
        {note && (
          <div className="hc-cs-hint" data-tone={note.tone} role="status">
            <Info size={14} aria-hidden="true" /><span>{note.text}</span>
            <button type="button" className="hc-cs-x" aria-label={t("action.close")} onClick={() => setNote(null)}><X size={14} aria-hidden="true" /></button>
          </div>
        )}
        {clipOpen && graphical && (
          <div className="hc-cs-clip" role="group" aria-label={t("cs.clipboard")}>
            <div>
              <label className="hc-cs-label" htmlFor="hc-cs-type">{t("cs.sendText")}</label>
              <textarea id="hc-cs-type" className="nx-inp nx-mono" rows={2} maxLength={MAX_TYPED} autoComplete="off" spellCheck={false} placeholder={t("vc.typePh")} value={typed} onChange={(e) => setTyped(e.target.value)} />
              <div className="hc-cs-row"><span className="hc-cs-muted">{t("vc.typeHelp")}</span><button type="button" className="nx-btn nx-btn--primary nx-btn--sm" disabled={!typed || busyTyping || !connected} onClick={typeIntoVm}>{busyTyping ? t("vc.typing") : t("vc.typeGo")}</button></div>
            </div>
            <div>
              <span className="hc-cs-label">{t("vc.guestClip")}</span>
              {guestClip ? <>
                <code className="nx-mono hc-cs-guestclip">{guestClip.slice(0, 500)}</code>
                <div className="hc-cs-row"><span /><button type="button" className="nx-btn nx-btn--sm" onClick={() => navigator.clipboard?.writeText(guestClip)}><Copy size={14} aria-hidden="true" /> {t("action.copy")}</button></div>
              </> : <p className="hc-cs-muted">{t("cs.noGuestClip")}</p>}
            </div>
          </div>
        )}
        <div ref={stage} className="hc-cs-stage" data-mode={mode} data-focus={focused || undefined} data-scale={scale}
          onFocusCapture={() => setFocused(true)} onBlurCapture={(e) => { if (!stage.current?.contains(e.relatedTarget)) setFocused(false); }}>
          <div ref={screen} className="hc-cs-screen" role="region" aria-label={t(MODES.find((m) => m.id === mode).label)} hidden={!!overlay && status !== "connecting"} />
          {overlay}
          {connected && (
            <p className="hc-cs-focus" aria-live="polite">{focused ? t("cs.keysCaptured") : t("cs.clickToType")}</p>
          )}
        </div>
      </div>
      <WorkstationAccess vm={vm} open={wsOpen} onClose={() => setWsOpen(false)} />
    </div>
  );
}
