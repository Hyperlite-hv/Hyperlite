import { useCallback, useEffect, useState } from "react";
import { Server, TriangleAlert } from "lucide-react";
import { createHostTerminalTicket, fetchDashboardSummary } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { Empty } from "../components/ui";
import StandaloneWindow from "./StandaloneWindow";
import LiveTerminal from "./LiveTerminal";

// /host-shell: the root shell of the host that runs Hyperlite, in a window of its own (administrators only, like the
// backend). Opened explicitly from the node page, it connects by itself.
export default function HostShellWindow() {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const [host, setHost] = useState("");
  useEffect(() => { fetchDashboardSummary().then((d) => setHost(d?.hyperviseur?.nom || "")).catch(() => {}); }, []);
  const getUrl = useCallback(async () => `/host/terminal?ticket=${encodeURIComponent((await createHostTerminalTicket()).ticket)}`, []);
  const title = host ? `${t("nn.shell")} · ${host}` : t("nn.shell");
  return (
    <StandaloneWindow title={title} icon={Server}>
      {caps.admin
        ? <LiveTerminal getUrl={getUrl} label={t("nn.shell")} note={<div className="nx-bn" data-tone="warning" role="note"><TriangleAlert size={16} aria-hidden="true" /><span className="nx-bn-t"><b>{t("nn.shellWarn")}</b> {t("nn.shellHelp", { name: host || "—" })}</span></div>} />
        : <div className="nx-card2"><Empty title={t("vc.adminOnlyTitle")} text={t("vc.adminOnlyHelp")} /></div>}
    </StandaloneWindow>
  );
}
