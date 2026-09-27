import { TerminalSquare, ExternalLink, ShieldAlert } from "lucide-react";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

// Host shell launcher (see app/routers/host.py + HostShellWindow.jsx), the same
// principle as VMConsoleTab: it opens in a separate window rather than in the
// current tab, to keep the dashboard usable while the shell session stays open
// next to it.
export default function NodeShellTab({ resource: node }) {
  const isAdmin = useAuthStore(selectIsAdmin);

  if (!isAdmin) {
    return <p className="text-sm text-muted-foreground">Host shell reserved for the admin role.</p>;
  }
  if (!node) return null;

  function openWindow() {
    window.open("/host-shell", "hyperlite-host-shell", "width=1100,height=750,noopener");
  }

  return (
    <Card className="flex flex-col items-center gap-3 p-10 text-center">
      <TerminalSquare size={28} className="text-muted-foreground" />
      <p className="text-sm text-foreground/80 max-w-md">
        Opens an interactive root shell directly on <strong>{node.nom}</strong>, the physical machine. Every session is logged (created/closed) and visible in the Tasks tab.
      </p>
      <div className="flex items-center gap-1.5 text-xs text-status-error">
        <ShieldAlert size={13} /> Root-equivalent access: use with care.
      </div>
      <Button onClick={openWindow}>
        <ExternalLink /> Open in a new window
      </Button>
    </Card>
  );
}
