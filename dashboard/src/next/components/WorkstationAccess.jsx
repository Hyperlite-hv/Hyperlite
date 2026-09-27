import { useEffect, useState } from "react";
import { Copy, Download, MonitorSmartphone, SquareTerminal } from "lucide-react";
import { fetchWorkstationConfig } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import { SideDrawer } from "./ui";

// Platform of this browser, to offer the matching client first.
function localPlatform() {
  const p = (navigator.userAgentData?.platform || navigator.platform || "").toLowerCase();
  if (p.includes("win")) return "windows-amd64";
  if (p.includes("mac")) return "darwin-arm64";
  return "linux-amd64";
}
const PLATFORM_KEY = { windows: "ws.os.windows", darwin: "ws.os.mac", linux: "ws.os.linux" };

function Cmd({ text, label }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const copy = () => navigator.clipboard?.writeText(text).then(() => pushToast({ kind: "success", title: t("ws.copied") })).catch(() => {});
  return (
    <div className="nx-ws-cmd">
      <code aria-label={label}>{text}</code>
      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("ws.copy", { what: label })} title={t("action.copy")} onClick={copy}><Copy size={14} aria-hidden="true" /></button>
    </div>
  );
}

// "From your workstation" panel of a VM: open SSH or remote desktop in the workstation's own tools through the
// hyperlite client (a tunnel over the server's HTTPS port), with the one-time set-up of the client.
export default function WorkstationAccess({ vm, open, onClose }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [config, setConfig] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!open || config) return;
    fetchWorkstationConfig().then(setConfig).catch((e) => setError(errorMessage(e)));
  }, [open, config]);

  const server = window.location.origin;
  const qs = `?server=${encodeURIComponent(server)}`;
  const vmName = vm.nom;
  const windowsGuest = /windows/i.test(vm.os || "");
  const ports = config?.tunnel_ports || [];
  const sshAllowed = ports.includes(22);
  const rdpAllowed = ports.includes(3389);
  const here = localPlatform();
  const downloads = config?.downloads || [];
  const mine = downloads.find((d) => d.platform === here);
  const others = downloads.filter((d) => d !== mine);
  const dlLabel = (d) => `${t(PLATFORM_KEY[d.platform.split("-")[0]])} (${d.platform.split("-")[1] === "arm64" ? "ARM" : "x64"})`;

  return (
    <SideDrawer open={open} title={t("ws.title")} onClose={onClose}>
      <p className="nx-muted" style={{ margin: 0 }}>{t("ws.lead")}</p>
      {error && <p className="nx-f-h is-error" role="alert">{error}</p>}
      {config && ports.length === 0 && <div className="nx-bn" data-tone="info" role="status"><span className="nx-bn-t">{t("ws.disabled")}</span></div>}
      {config && ports.length > 0 && (
        <>
          <div className="nx-inline">
            {sshAllowed && <a className="nx-btn nx-btn--primary" href={`hyperlite://ssh/${encodeURIComponent(vmName)}${qs}`}><SquareTerminal size={15} aria-hidden="true" />{t("ws.openSsh")}</a>}
            {rdpAllowed && <a className={`nx-btn${windowsGuest || !sshAllowed ? " nx-btn--primary" : ""}`} href={`hyperlite://rdp/${encodeURIComponent(vmName)}${qs}`}><MonitorSmartphone size={15} aria-hidden="true" />{t("ws.openRdp")}</a>}
          </div>
          <p className="nx-f-h" style={{ margin: 0 }}>{t("ws.linkHint")}</p>

          <h3 className="nx-h3">{t("ws.setupTitle")}</h3>
          <ol className="nx-ws-steps">
            <li>
              <div>{t("ws.step1")}</div>
              {downloads.length === 0 ? <p className="nx-muted" style={{ margin: "0.4rem 0 0" }}>{t("ws.noDownloads")}</p> : (
                <div className="nx-ws-dl" style={{ marginTop: "0.4rem" }}>
                  {mine && <a className="nx-btn nx-btn--sm" href={`/downloads/hyperlite/${mine.platform}`} download={mine.filename}><Download size={14} aria-hidden="true" />{dlLabel(mine)} · {formatSizeMb(mine.size / 1048576, lang)}</a>}
                  {others.map((d) => <a key={d.platform} className="nx-btn nx-btn--ghost nx-btn--sm" href={`/downloads/hyperlite/${d.platform}`} download={d.filename}>{dlLabel(d)}</a>)}
                </div>
              )}
              {mine && <p className="nx-f-h" style={{ margin: "0.4rem 0 0" }}>SHA-256 <span className="nx-mono" style={{ overflowWrap: "anywhere" }}>{mine.sha256}</span></p>}
            </li>
            <li><div>{t("ws.step2")}</div><Cmd text={`hyperlite login ${server}`} label={t("ws.cmdLogin")} /></li>
            <li><div>{t("ws.step3")}</div><Cmd text="hyperlite setup" label={t("ws.cmdSetup")} /></li>
          </ol>

          <h3 className="nx-h3">{t("ws.cliTitle")}</h3>
          {sshAllowed && <Cmd text={`hyperlite ssh ${vmName}`} label={t("ws.cmdSsh")} />}
          {rdpAllowed && <Cmd text={`hyperlite rdp ${vmName}`} label={t("ws.cmdRdp")} />}
          <Cmd text={`hyperlite tunnel ${vmName} ${ports[0]} --listen 127.0.0.1:${ports[0] === 22 ? 2222 : 13389}`} label={t("ws.cmdTunnel")} />
          <p className="nx-f-h" style={{ margin: 0 }}>{t("ws.ports", { ports: ports.join(", "), min: Math.round(config.tunnel_idle_timeout_s / 60) })}</p>
          <p className="nx-f-h" style={{ margin: 0 }}>{t("ws.security", { days: config.cli_token_days })}</p>
        </>
      )}
    </SideDrawer>
  );
}
