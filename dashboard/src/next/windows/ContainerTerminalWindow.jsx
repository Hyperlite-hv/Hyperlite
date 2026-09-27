import { useCallback } from "react";
import { useParams } from "react-router-dom";
import { Box } from "lucide-react";
import { createContainerTerminalTicket } from "../../api/client";
import { useT } from "../i18n";
import StandaloneWindow from "./StandaloneWindow";
import LiveTerminal from "./LiveTerminal";

// /container-terminal/:name: the terminal of an LXC container in a window of its own, connected by itself.
export default function ContainerTerminalWindow() {
  const t = useT();
  const { name } = useParams();
  const getUrl = useCallback(async () => {
    const ticket = await createContainerTerminalTicket(name);
    return `/containers/${encodeURIComponent(name)}/terminal?ticket=${encodeURIComponent(ticket.ticket)}`;
  }, [name]);
  return (
    <StandaloneWindow title={`${t("ct.terminal")} · ${name}`} icon={Box}>
      <LiveTerminal getUrl={getUrl} label={t("ct.terminal")} />
    </StandaloneWindow>
  );
}
