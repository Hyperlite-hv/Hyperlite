import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { MonitorSmartphone, RefreshCw } from "lucide-react";
import { createRdpTicket, fetchRdpAvailable } from "../../api/client";
import { wsUrl } from "../../utils/loadXterm";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Field } from "../components/ui";
import StandaloneWindow from "./StandaloneWindow";

// /rdp/:name?node=<node>: the VM's remote desktop in a window of its own (docs/design/console-2.md, section 8). The node
// speaks RDP to the guest and shows it as a VNC display, drawn here by noVNC as the console is. The password goes once
// with the ticket request and is not kept by this page after the connection starts.
export default function RdpWindow() {
  const t = useT();
  const { name } = useParams();
  const [params] = useSearchParams();
  const node = params.get("node") || null;
  const screen = useRef(null);
  const rfb = useRef(null);
  const [avail, setAvail] = useState(null);
  const [f, setF] = useState({ username: "", password: "", domain: "" });
  const [state, setState] = useState("form"); // form, connecting, connected, ended
  const [error, setError] = useState(null);
  useEffect(() => { fetchRdpAvailable(name, node).then(setAvail).catch((e) => setAvail({ available: false, error: errorMessage(e) })); }, [name, node]);
  useEffect(() => () => { try { rfb.current?.disconnect(); } catch { /* closed */ } }, []);
  const connect = useCallback(async (e) => {
    e?.preventDefault();
    setState("connecting"); setError(null);
    try {
      const box = screen.current.getBoundingClientRect();
      const ticket = await createRdpTicket(name, node, { ...f, width: Math.round(box.width) || window.innerWidth, height: Math.round(box.height) || window.innerHeight - 60 });
      setF((x) => ({ ...x, password: "" }));
      const mod = await import(/* @vite-ignore */ new URL("/novnc/core/rfb.js", window.location.origin).href);
      screen.current.innerHTML = "";
      const r = new mod.default(screen.current, wsUrl(`/vms/${encodeURIComponent(name)}/rdp?ticket=${encodeURIComponent(ticket.ticket)}`));
      r.scaleViewport = true;
      r.focusOnClick = true;
      rfb.current = r;
      r.addEventListener("connect", () => { setState("connected"); r.focus(); });
      r.addEventListener("disconnect", (ev) => { setState("ended"); if (!ev.detail?.clean) setError(t("rdp.dropped")); });
      r.addEventListener("securityfailure", (ev) => { setError(ev.detail?.reason || t("rdp.dropped")); });
      // The close reason the server gives (a refused sign-in, a changed certificate) reaches noVNC's socket.
      r._sock?._websocket?.addEventListener?.("close", (ev) => { if (ev.reason) setError(ev.reason); });
    } catch (err) { setState("form"); setError(errorMessage(err)); }
  }, [f, name, node, t]);
  return (
    <StandaloneWindow title={t("rdp.title", { name })} icon={MonitorSmartphone}>
      <div className="hc-rdp">
        {(state === "form" || (state === "ended")) && (
          <form className="hc-rdp-form" onSubmit={connect}>
            <h2>{t("rdp.title", { name })}</h2>
            {avail && !avail.available && <p className="nx-err" role="alert">{avail.error || t("rdp.missing", { packages: avail.packages || "" })}</p>}
            {state === "ended" && <p className="nx-muted">{t("rdp.ended")}</p>}
            {error && <p className="nx-err" role="alert">{error}</p>}
            <p className="nx-muted">{t("rdp.note")}</p>
            <Field label={t("rdp.user")}>{(p) => <input {...p} className="nx-inp" autoComplete="username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required autoFocus />}</Field>
            <Field label={t("rdp.password")}>{(p) => <input {...p} type="password" className="nx-inp" autoComplete="current-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />}</Field>
            <Field label={t("rdp.domain")} hint={t("rdp.domainHint")}>{(p) => <input {...p} className="nx-inp" value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} />}</Field>
            <button type="submit" className="nx-btn nx-btn--primary" disabled={!f.username || (avail && !avail.available)}>{state === "ended" ? <><RefreshCw size={14} aria-hidden="true" /> {t("rdp.reconnect")}</> : t("rdp.connect")}</button>
          </form>
        )}
        {state === "connecting" && <p className="hc-rdp-wait" role="status">{t("rdp.connecting")}</p>}
        <div ref={screen} className="hc-rdp-screen" hidden={state !== "connected" && state !== "connecting"} />
      </div>
    </StandaloneWindow>
  );
}
