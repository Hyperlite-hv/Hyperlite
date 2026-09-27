import { useEffect, useState } from "react";
import { Copy, Download, FileDown, MonitorSmartphone, SquareTerminal } from "lucide-react";
import { fetchWorkstationConfig, fetchVmAccess } from "../../api/client";
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

// A remote desktop file for a directly reachable Windows VM: Windows opens it with its own client, nothing to install.
function downloadRdp(vmName, ip, user) {
  const lines = [`full address:s:${ip}:3389`, "prompt for credentials:i:1", ...(user ? [`username:s:${user}`] : [])];
  const url = URL.createObjectURL(new Blob([lines.join("\r\n") + "\r\n"], { type: "application/x-rdp" }));
  const a = document.createElement("a");
  a.href = url; a.download = `${vmName}.rdp`; a.click();
  URL.revokeObjectURL(url);
}

// "From your workstation" panel of a VM, simplest path first:
// - a VM on a bridged network of the site is reachable like any machine of the LAN: the SSH command to copy and a
//   remote desktop file, nothing to install (the model of the clouds' "Connect" button behind a VPN);
// - otherwise, the hyperlite client opens a tunnel over the server's HTTPS port: one click once it is installed
//   (a double-click on the downloaded file), the first click asking to approve this computer.
export default function WorkstationAccess({ vm, open, onClose }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [config, setConfig] = useState(null);
  const [access, setAccess] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!open) return;
    setError(null);
    Promise.all([fetchWorkstationConfig(), fetchVmAccess(vm.nom)])
      .then(([c, a]) => { setConfig(c); setAccess(a); })
      .catch((e) => setError(errorMessage(e)));
  }, [open, vm.nom, vm.etat]);

  const server = window.location.origin;
  const qs = `?server=${encodeURIComponent(server)}`;
  const vmName = vm.nom;
  const windowsGuest = /windows/i.test(vm.os || "");
  const ports = config?.tunnel_ports || [];
  const sshTunnel = ports.includes(22);
  const rdpTunnel = ports.includes(3389);
  const here = localPlatform();
  const downloads = config?.downloads || [];
  const mine = downloads.find((d) => d.platform === here);
  const others = downloads.filter((d) => d !== mine);
  const dlLabel = (d) => `${t(PLATFORM_KEY[d.platform.split("-")[0]])} (${d.platform.split("-")[1] === "arm64" ? "ARM" : "x64"})`;
  const direct = access?.direct && access.ip;
  const sshDirect = direct ? `ssh ${access.ssh_user ? `${access.ssh_user}@` : ""}${access.ip}` : null;
  const ready = config && access;

  return (
    <SideDrawer open={open} title={t("ws.title")} onClose={onClose}>
      {error && <p className="nx-f-h is-error" role="alert">{error}</p>}
      {!ready && !error && <p className="nx-muted" role="status">{t("loading")}</p>}
      {ready && vm.etat !== "actif" && <div className="nx-bn" data-tone="info" role="status"><span className="nx-bn-t">{t("vc.mustRun")}</span></div>}

      {ready && direct && (
        <section className="nx-stack" aria-labelledby="ws-direct">
          <h3 className="nx-h3" id="ws-direct">{t("ws.directTitle")}</h3>
          <p className="nx-muted" style={{ margin: 0 }}>{t("ws.directLead")}</p>
          <Cmd text={sshDirect} label={t("ws.cmdSshDirect")} />
          {windowsGuest && <div><button type="button" className="nx-btn" onClick={() => downloadRdp(vmName, access.ip, access.ssh_user)}><FileDown size={15} aria-hidden="true" />{t("ws.rdpFile")}</button></div>}
        </section>
      )}

      {ready && ports.length > 0 && (
        <section className="nx-stack" aria-labelledby="ws-tunnel">
          <h3 className="nx-h3" id="ws-tunnel">{t(direct ? "ws.tunnelTitleAlt" : "ws.tunnelTitle")}</h3>
          <div className="nx-inline">
            {sshTunnel && <a className={`nx-btn${direct ? "" : " nx-btn--primary"}`} href={`hyperlite://ssh/${encodeURIComponent(vmName)}${qs}`}><SquareTerminal size={15} aria-hidden="true" />{t("ws.openSsh")}</a>}
            {rdpTunnel && <a className={`nx-btn${!direct && (windowsGuest || !sshTunnel) ? " nx-btn--primary" : ""}`} href={`hyperlite://rdp/${encodeURIComponent(vmName)}${qs}`}><MonitorSmartphone size={15} aria-hidden="true" />{t("ws.openRdp")}</a>}
          </div>
          <div className="nx-ws-first">
            <p style={{ margin: 0 }}><b>{t("ws.firstTime")}</b> {t("ws.firstTimeHelp")}</p>
            {mine
              ? <div><a className="nx-btn nx-btn--sm" href={`/downloads/hyperlite/${mine.platform}`} download={mine.filename}><Download size={14} aria-hidden="true" />{t("ws.download", { os: dlLabel(mine), size: formatSizeMb(mine.size / 1048576, lang) })}</a></div>
              : <p className="nx-muted" style={{ margin: 0 }}>{t("ws.noDownloads")}</p>}
          </div>
        </section>
      )}

      {ready && ports.length === 0 && !direct && <div className="nx-bn" data-tone="info" role="status"><span className="nx-bn-t">{t("ws.disabled")}</span></div>}

      {ready && ports.length > 0 && (
        <details className="nx-ws-more">
          <summary>{t("ws.advanced")}</summary>
          <div className="nx-stack">
            {others.length > 0 && (
              <div>
                <div className="nx-f-label">{t("ws.otherSystems")}</div>
                <div className="nx-ws-dl">{others.map((d) => <a key={d.platform} className="nx-btn nx-btn--ghost nx-btn--sm" href={`/downloads/hyperlite/${d.platform}`} download={d.filename}>{dlLabel(d)}</a>)}</div>
              </div>
            )}
            {mine && <p className="nx-f-h" style={{ margin: 0 }}>SHA-256 ({dlLabel(mine)}) <span className="nx-mono" style={{ overflowWrap: "anywhere" }}>{mine.sha256}</span></p>}
            <div className="nx-f-label">{t("ws.cliTitle")}</div>
            {sshTunnel && <Cmd text={`hyperlite ssh ${vmName}`} label={t("ws.cmdSsh")} />}
            {rdpTunnel && <Cmd text={`hyperlite rdp ${vmName}`} label={t("ws.cmdRdp")} />}
            <Cmd text={`hyperlite tunnel ${vmName} ${ports[0]} --listen 127.0.0.1:${ports[0] === 22 ? 2222 : 13389}`} label={t("ws.cmdTunnel")} />
            <Cmd text={`hyperlite login ${server}`} label={t("ws.cmdLogin")} />
            <p className="nx-f-h" style={{ margin: 0 }}>{t("ws.ports", { ports: ports.join(", "), min: Math.round(config.tunnel_idle_timeout_s / 60) })}</p>
            <p className="nx-f-h" style={{ margin: 0 }}>{t("ws.security", { days: config.cli_token_days })}</p>
          </div>
        </details>
      )}
    </SideDrawer>
  );
}
