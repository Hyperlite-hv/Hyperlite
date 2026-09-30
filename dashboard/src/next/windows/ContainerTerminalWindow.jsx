import { useCallback } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Box } from "lucide-react";
import { createContainerShellTicket, createContainerTerminalTicket } from "../../api/client";
import { useT } from "../i18n";
import StandaloneWindow from "./StandaloneWindow";
import LiveTerminal from "./LiveTerminal";

// /container-terminal/:name: the terminal of an LXC container in a window of its own, connected by itself: SSH as
// its user, or with ?shell=1 a root shell inside the container, Docker or LXC (administrators, like docker exec).
export default function ContainerTerminalWindow() {
  const t = useT();
  const { name } = useParams();
  const shell = useSearchParams()[0].get("shell") === "1";
  const getUrl = useCallback(async () => {
    const ticket = await (shell ? createContainerShellTicket(name) : createContainerTerminalTicket(name));
    return `/containers/${encodeURIComponent(name)}/${shell ? "shell" : "terminal"}?ticket=${encodeURIComponent(ticket.ticket)}`;
  }, [name, shell]);
  const label = t(shell ? "ct.shell" : "ct.terminal");
  return (
    <StandaloneWindow title={`${label} · ${name}`} icon={Box}>
      <LiveTerminal getUrl={getUrl} label={label} />
    </StandaloneWindow>
  );
}
