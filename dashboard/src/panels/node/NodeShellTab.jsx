import { TerminalSquare, ExternalLink, ShieldAlert } from "lucide-react";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";

// Host shell launcher (see app/routers/host.py + HostShellWindow.jsx), the same
// principle as VMConsoleTab: it opens in a separate window rather than in the
// current tab, to keep the dashboard usable while the shell session stays open
// next to it.
export default function NodeShellTab({ resource: node }) {
  const isAdmin = useAuthStore(selectIsAdmin);

  if (!isAdmin) {
    return <p className="text-sm text-anthracite-400">Host shell reserved for the admin role.</p>;
  }
  if (!node) return null;

  function openWindow() {
    window.open("/host-shell", "hyperlite-host-shell", "width=1100,height=750,noopener");
  }

  return (
    <div className="card flex flex-col items-center gap-3 p-10 text-center">
      <TerminalSquare size={28} className="text-anthracite-400" />
      <p className="text-sm text-anthracite-300 max-w-md">
        Opens an interactive root shell directly on <strong>{node.nom}</strong>, the physical machine. Every session is logged (created/closed) and visible in the Tasks tab.
      </p>
      <div className="flex items-center gap-1.5 text-xs text-status-error">
        <ShieldAlert size={13} /> Root-equivalent access: use with care.
      </div>
      <button className="btn-primary" onClick={openWindow}>
        <ExternalLink size={14} /> Open in a new window
      </button>
    </div>
  );
}
