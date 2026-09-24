import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Server } from "lucide-react";
import ConsolePanel from "../components/ConsolePanel";
import LoginScreen from "../auth/LoginScreen";
import { useAuthStore } from "../store/useAuthStore";
import { fetchVM } from "../api/client";
import { Button } from "@/components/ui/button";

// Standalone page (no AppShell, no sidebar/header) opened in a separate
// window/tab through window.open(), see VMConsoleTab.jsx. Same origin as the rest
// of the app, so the same session (JWT in localStorage, re-read by useAuthStore
// like everywhere else).
export default function ConsoleWindow() {
  const { name } = useParams();
  const [searchParams] = useSearchParams();
  const initialMode = searchParams.get("mode") === "terminal" ? "terminal" : "vnc";

  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);
  const [vm, setVm] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => { restoreSession(); }, [restoreSession]);

  useEffect(() => {
    if (status !== "authenticated") return;
    fetchVM(name).then(setVm).catch((e) => setError(e.message));
    const id = setInterval(() => fetchVM(name).then(setVm).catch(() => {}), 5000);
    return () => clearInterval(id);
  }, [name, status]);

  if (status === "checking") {
    return <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">Checking the session...</div>;
  }
  if (status === "anonymous") {
    return <LoginScreen />;
  }

  return (
    <div className="flex h-screen flex-col bg-background p-3 gap-3">
      <div className="flex items-center gap-2 shrink-0">
        <Server size={15} className="text-accent-blue" />
        <span className="text-sm font-semibold text-foreground">{name}</span>
        {vm?.etat && <span className="text-xs text-muted-foreground">({vm.etat})</span>}
        <Button variant="secondary" className="ml-auto" onClick={() => window.close()}>Close the window</Button>
      </div>
      {error && <p className="text-xs text-status-error">{error}</p>}
      <div className="flex-1 min-h-0">
        <ConsolePanel vmName={name} vmActive={vm?.etat === "actif"} initialMode={initialMode} />
      </div>
    </div>
  );
}
