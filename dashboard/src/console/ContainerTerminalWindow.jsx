import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { Box } from "lucide-react";
import ContainerShellPanel from "../components/ContainerShellPanel";
import LoginScreen from "../auth/LoginScreen";
import { useAuthStore } from "../store/useAuthStore";
import { Button } from "@/components/ui/button";

// Standalone page opened through window.open() (see ContainersTab.jsx), the same
// principle as ConsoleWindow.jsx / HostShellWindow.jsx: same origin, so the same
// session (JWT in localStorage, re-read by useAuthStore).
export default function ContainerTerminalWindow() {
  const { name } = useParams();
  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);

  useEffect(() => { restoreSession(); }, [restoreSession]);

  if (status === "checking") {
    return <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">Checking the session...</div>;
  }
  if (status === "anonymous") {
    return <LoginScreen />;
  }

  return (
    <div className="flex h-screen flex-col bg-background p-3 gap-3">
      <div className="flex items-center gap-2 shrink-0">
        <Box size={15} className="text-accent-blue" />
        <span className="text-sm font-semibold text-foreground">Terminal — {name}</span>
        <Button variant="secondary" className="ml-auto" onClick={() => window.close()}>Close the window</Button>
      </div>
      <div className="flex-1 min-h-0">
        <ContainerShellPanel name={name} />
      </div>
    </div>
  );
}
