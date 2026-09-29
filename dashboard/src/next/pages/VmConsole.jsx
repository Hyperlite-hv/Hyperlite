import { useCallback, useEffect, useRef, useState } from "react";
import { termOptions } from "../lib/prefs";
import { ChevronDown, ClipboardPaste, Copy, ExternalLink, Keyboard, Laptop, Maximize, TriangleAlert, X } from "lucide-react";
import { createConsoleTicket, createTerminalTicket } from "../../api/client";
import { ensureXtermLoaded, wsUrl } from "../../utils/loadXterm";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { consoleUrl } from "../lib/vmActions";
import { apiNode, vmKey } from "../lib/vmId";
import { Empty } from "../components/ui";
import WorkstationAccess from "../components/WorkstationAccess";
import Menu, { MenuItem } from "../components/Menu";
import { KEY_COMBOS, MAX_TYPED, sendCombo, textToKeysyms } from "../lib/typeText";

const RETRY_S = 5;
// About a minute of automatic retries. Past that, or on a refusal (a 4xx: no such VM, no privilege...), retrying
// cannot help: the console stops and says why, with a Retry button. Each attempt asks for a ticket, which the server
// audits: an endless loop filled the audit log for as long as the tab stayed open.
const MAX_ATTEMPTS = 12;

// Console of a VM: the graphical console (VNC through noVNC) or the SSH terminal (xterm, administrators only, like
// the backend), over the ticket + WebSocket relay. There is no Connect button: it connects by itself as soon as
// the VM runs, and reconnects every few seconds after an error or a dropped connection (a VM whose IP address is
// not known yet, a reboot...), up to MAX_ATTEMPTS. Used in the VM page and, with `standalone`, as the separate
// console window. The VM's node goes with every ticket: a remote VM's console is relayed through its node.
export default function VmConsole({ resource: vm, standalone = false, initialMode = "vnc" }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const [mode, setMode] = useState(initialMode);
  const [retryIn, setRetryIn] = useState(null);
  const [wsOpen, setWsOpen] = useState(false);
  const attempts = useRef(0);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const [gaveUp, setGaveUp] = useState(false); // automatic retries stopped: only the Retry button reconnects
  const [keysOpen, setKeysOpen] = useState(false);
  const keysBtn = useRef(null);
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState("");
  const [busyTyping, setBusyTyping] = useState(false);
  const [guestClip, setGuestClip] = useState(null);
  const screen = useRef(null);
  const frame = useRef(null);
  const rfb = useRef(null);
  const term = useRef(null);
  const ws = useRef(null);
  const onResize = useRef(null);
  const name = vm?.nom;
  const node = apiNode(vm?.node);
  const running = vm?.etat === "actif";
  // A refusal will not change by asking again every few seconds.
  const failed = (e) => { setError(errorMessage(e)); setStatus("error"); if (e?.status >= 400 && e.status < 500) setGaveUp(true); };
  const terminal = mode === "terminal";

  const cleanup = useCallback(() => {
    try { rfb.current?.disconnect(); } catch { /* already closed */ }
    try { ws.current?.close(); } catch { /* already closed */ }
    try { term.current?.dispose(); } catch { /* already disposed */ }
    if (onResize.current) window.removeEventListener("resize", onResize.current);
    rfb.current = null; ws.current = null; term.current = null; onResize.current = null;
    if (screen.current) screen.current.innerHTML = "";
    setStatus("idle");
  }, []);

  const connectVnc = useCallback(async () => {
    setStatus("connecting"); setError(null);
    try {
      const ticket = await createConsoleTicket(name, node);
      const url = wsUrl(`/vms/${encodeURIComponent(name)}/console?ticket=${encodeURIComponent(ticket.ticket)}`);
      // noVNC lives in public/novnc, outside the bundle: loaded as is at runtime.
      const mod = await import(/* @vite-ignore */ new URL("/novnc/core/rfb.js", window.location.origin).href);
      screen.current.innerHTML = "";
      const r = new mod.default(screen.current, url);
      r.scaleViewport = true;
      rfb.current = r;
      r.addEventListener("connect", () => { attempts.current = 0; setStatus("connected"); r.scaleViewport = true; });
      r.addEventListener("disconnect", () => setStatus("idle"));
      r.addEventListener("credentialsrequired", () => { setError(t("vc.credentials")); setStatus("error"); });
      // A guest with a clipboard agent sends what it copies: offered here to copy on this computer.
      r.addEventListener("clipboard", (e) => setGuestClip(e.detail?.text || null));
    } catch (e) { failed(e); }
  }, [name, node, t]); // failed() only calls state setters

  async function connectTerminal() {
    setStatus("connecting"); setError(null);
    try {
      await ensureXtermLoaded();
      const ticket = await createTerminalTicket(name, node);
      screen.current.innerHTML = "";
      // xterm is a UMD global; its theme is the graphite of the navigation column.
      // eslint-disable-next-line no-undef
      const tm = new Terminal(termOptions());
      // eslint-disable-next-line no-undef
      const fit = new FitAddon.FitAddon();
      tm.loadAddon(fit); tm.open(screen.current); fit.fit(); term.current = tm;
      const sock = new WebSocket(wsUrl(`/vms/${encodeURIComponent(name)}/terminal?ticket=${encodeURIComponent(ticket.ticket)}`));
      ws.current = sock;
      sock.onopen = () => { attempts.current = 0; setStatus("connected"); fit.fit(); sock.send("\x00" + JSON.stringify({ cols: tm.cols, rows: tm.rows })); };
      sock.onmessage = (ev) => tm.write(ev.data);
      sock.onclose = () => { tm.write(`\r\n\x1b[33m[${t("vc.closedRetry", { s: RETRY_S })}]\x1b[0m\r\n`); setStatus("idle"); };
      sock.onerror = () => setError(t("vc.termError"));
      tm.onData((d) => { if (sock.readyState === WebSocket.OPEN) sock.send(d); });
      tm.onResize(({ cols, rows }) => { if (sock.readyState === WebSocket.OPEN) sock.send("\x00" + JSON.stringify({ cols, rows })); });
      onResize.current = () => fit.fit();
      window.addEventListener("resize", onResize.current);
    } catch (e) { failed(e); }
  }

  useEffect(() => cleanup, [cleanup, name, node, mode]);
  useEffect(() => { attempts.current = 0; setGaveUp(false); }, [name, node, mode]);
  // Automatic connection: right away the first time, then every RETRY_S seconds while it fails or after a drop,
  // as long as the VM runs and this console is allowed (the terminal is for administrators).
  const allowed = running && !(terminal && !caps.admin);
  useEffect(() => {
    if (!allowed || gaveUp || (status !== "idle" && status !== "error")) { setRetryIn(null); return undefined; }
    if (attempts.current >= MAX_ATTEMPTS) { setGaveUp(true); setRetryIn(null); return undefined; }
    const delay = attempts.current === 0 ? 0 : RETRY_S;
    attempts.current += 1;
    setRetryIn(delay || null);
    let left = delay;
    const tick = delay ? setInterval(() => { left -= 1; setRetryIn(left > 0 ? left : null); }, 1000) : null;
    const go = setTimeout(() => { if (tick) clearInterval(tick); setRetryIn(null); if (terminal) connectTerminal(); else connectVnc(); }, delay * 1000);
    return () => { clearTimeout(go); if (tick) clearInterval(tick); };
  }, [allowed, gaveUp, status, mode, name, node]); // eslint-disable-line react-hooks/exhaustive-deps
  // Types the text as key presses (works at a login prompt, no agent needed), a few milliseconds apart so that a
  // slow guest does not drop any; a guest with a clipboard agent also receives it as its clipboard.
  async function typeIntoVm() {
    const r = rfb.current;
    if (!r || !typed) return;
    setBusyTyping(true);
    try {
      const { default: keysyms } = await import(/* @vite-ignore */ new URL("/novnc/core/input/keysymdef.js", window.location.origin).href);
      try { r.clipboardPasteFrom(typed); } catch { /* no clipboard support on this server: typing is enough */ }
      for (const sym of textToKeysyms(typed, keysyms.lookup)) {
        if (rfb.current !== r) break;
        r.sendKey(sym, null);
        await new Promise((res) => setTimeout(res, 8));
      }
      setTyped(""); setTyping(false);
    } finally { setBusyTyping(false); }
  }
  const retryNow = () => { cleanup(); attempts.current = 0; setError(null); setGaveUp(false); };

  if (!vm) return null;
  const openWindow = () => window.open(consoleUrl(vm, mode), `hyperlite-console-${vmKey(vm)}-${mode}`, "width=1100,height=750,noopener");
  const connected = status === "connected";
  const switchMode = (m) => { if (m !== mode) { cleanup(); attempts.current = 0; setError(null); setGaveUp(false); setMode(m); } };
  const stateLabel = connected ? t("nn.connected") : status === "connecting" ? t("nn.connecting") : retryIn ? t("vc.retryIn", { s: retryIn }) : gaveUp ? t("vc.stopped") : t("nn.notConnected");

  return (
    <>
      {terminal && caps.admin && <div className="nx-bn" data-tone="warning" role="note"><TriangleAlert size={16} aria-hidden="true" /><span className="nx-bn-t">{t("vc.sshWarn")}</span></div>}
      <div className={standalone ? "nx-console-standalone" : undefined}>
        <div className="nx-termbar">
          <div className="nx-seg2" role="group" aria-label={t("vc.mode")}>
            <button type="button" aria-pressed={!terminal} onClick={() => switchMode("vnc")}>{t("vc.vnc")}</button>
            <button type="button" aria-pressed={terminal} onClick={() => switchMode("terminal")}>{t("vc.ssh")}</button>
          </div>
          <span className="nx-st" data-tone={connected ? undefined : "offline"} style={{ fontSize: "var(--fs-125)" }}><span className="nx-dot" data-tone={connected ? "success" : error ? "warning" : "offline"} aria-hidden="true" /><span role="status">{stateLabel}</span></span>
          <span className="nx-sp" />
          {!terminal && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={!connected} onClick={() => rfb.current?.sendCtrlAltDel()}><Keyboard size={14} aria-hidden="true" />{t("vc.ctrlAltDel")}</button>}
          {!terminal && (
            <span className="nx-relative">
              <button ref={keysBtn} type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={!connected} aria-haspopup="menu" aria-expanded={keysOpen} onClick={() => setKeysOpen((o) => !o)}>{t("vc.keys")}<ChevronDown size={14} aria-hidden="true" /></button>
              <Menu open={keysOpen} onClose={() => setKeysOpen(false)} label={t("vc.keys")} returnFocusRef={keysBtn} style={{ top: "calc(100% + 4px)", right: 0 }}>
                {KEY_COMBOS.map((c) => <MenuItem key={c.id} onSelect={() => { setKeysOpen(false); if (rfb.current) sendCombo(rfb.current, c); }}>{t(`vc.key.${c.id}`)}</MenuItem>)}
              </Menu>
            </span>
          )}
          {!terminal && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={!connected} aria-expanded={typing} onClick={() => setTyping((v) => !v)}><ClipboardPaste size={14} aria-hidden="true" />{t("vc.typeText")}</button>}
          <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={!connected} onClick={() => frame.current?.requestFullscreen?.()}><Maximize size={14} aria-hidden="true" />{t("vc.fullscreen")}</button>
          <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setWsOpen(true)}><Laptop size={14} aria-hidden="true" />{t("ws.button")}</button>
          {standalone
            ? <button type="button" className="nx-btn nx-btn--sm" onClick={() => window.close()}><X size={14} aria-hidden="true" />{t("vc.closeWindow")}</button>
            : <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={!running || (terminal && !caps.admin)} onClick={openWindow}><ExternalLink size={14} aria-hidden="true" />{t("nn.openWindow")}</button>}
        </div>
        {!terminal && typing && connected && (
          <div className="nx-typebar" role="group" aria-label={t("vc.typeText")}>
            <textarea className="nx-inp nx-mono" rows={2} maxLength={MAX_TYPED} autoComplete="off" spellCheck={false} aria-label={t("vc.typeLabel")} placeholder={t("vc.typePh")} value={typed} onChange={(e) => setTyped(e.target.value)} />
            <div className="nx-inline">
              <span className="nx-f-h">{t("vc.typeHelp")}</span>
              <span className="nx-sp" />
              <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" disabled={!typed || busyTyping} onClick={typeIntoVm}>{busyTyping ? t("vc.typing") : t("vc.typeGo")}</button>
            </div>
          </div>
        )}
        {!terminal && guestClip && (
          <div className="nx-typebar" role="status">
            <span className="nx-f-h">{t("vc.guestClip")}</span>
            <code className="nx-mono nx-clip">{guestClip.slice(0, 300)}</code>
            <div className="nx-inline"><span className="nx-sp" />
              <button type="button" className="nx-btn nx-btn--sm" onClick={() => navigator.clipboard?.writeText(guestClip)}><Copy size={14} aria-hidden="true" />{t("action.copy")}</button>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setGuestClip(null)}>{t("action.close")}</button>
            </div>
          </div>
        )}
        {terminal && !caps.admin ? (
          <div className="nx-card2" style={{ borderRadius: "0 0 10px 10px" }}><Empty title={t("vc.adminOnlyTitle")} text={t("vc.adminOnlyHelp")} /></div>
        ) : (
          <div className="nx-termwrap nx-screen" ref={frame}>
            <div className="nx-term nx-term--screen" ref={screen} role="region" aria-label={terminal ? t("vc.ssh") : t("vc.vnc")} />
            {!connected && status !== "connecting" && !retryIn && !error && <p className="nx-term-hint">{running ? t("nn.connecting") : t("vc.mustRun")}</p>}
          </div>
        )}
        {error && <p className="nx-f-h is-error" role="alert">{error}</p>}
        {gaveUp && running && <div className="nx-inline" style={{ marginTop: "var(--space-2)" }}><span className="nx-f-h">{t("vc.gaveUp")}</span><button type="button" className="nx-btn nx-btn--sm" onClick={retryNow}>{t("action.retry")}</button></div>}
      </div>
      <WorkstationAccess vm={vm} open={wsOpen} onClose={() => setWsOpen(false)} />
    </>
  );
}
