import { useEffect, useState } from "react";
import { Server } from "lucide-react";
import HostShellPanel from "../components/HostShellPanel";
import LoginScreen from "../auth/LoginScreen";
import { useAuthStore, selectIsAdmin } from "../store/useAuthStore";
import { fetchDashboardSummary } from "../api/client";
import { Button } from "@/components/ui/button";

// Standalone page opened through window.open() (see NodeShellTab.jsx), the same
// principle as ConsoleWindow.jsx for VMs: same origin, so the same session (JWT in
// localStorage, re-read by useAuthStore).
export default function HostShellWindow() {
  const status = useAuthStore((s) => s.status);
  const isAdmin = useAuthStore(selectIsAdmin);
  const restoreSession = useAuthStore((s) => s.restoreSession);
  const [hostname, setHostname] = useState("the host");

  useEffect(() => { restoreSession(); }, [restoreSession]);

  useEffect(() => {
    if (status !== "authenticated") return;
    fetchDashboardSummary().then((d) => setHostname(d.hyperviseur?.nom || "the host")).catch(() => {});
  }, [status]);

  if (status === "checking") {
    return <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">Checking the session...</div>;
  }
  if (status === "anonymous") {
    return <LoginScreen />;
  }
  if (!isAdmin) {
    return <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">Host shell reserved for the admin role.</div>;
  }

  return (
    <div className="flex h-screen flex-col bg-background p-3 gap-3">
      <div className="flex items-center gap-2 shrink-0">
        <Server size={15} className="text-accent-blue" />
        <span className="text-sm font-semibold text-foreground">Host shell — {hostname}</span>
        <Button variant="secondary" className="ml-auto" onClick={() => window.close()}>Close the window</Button>
      </div>
      <div className="flex-1 min-h-0">
        <HostShellPanel hostname={hostname} />
      </div>
    </div>
  );
}
