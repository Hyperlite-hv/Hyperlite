import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppShell from "./layout/AppShell";
import LoginScreen from "./auth/LoginScreen";
import ConsoleWindow from "./console/ConsoleWindow";
import HostShellWindow from "./console/HostShellWindow";
import ContainerTerminalWindow from "./console/ContainerTerminalWindow";
import { useAuthStore } from "./store/useAuthStore";

// /console/:name and /host-shell have their own authentication gate
// (ConsoleWindow / HostShellWindow): these pages open in a separate window (see
// VMConsoleTab / NodeShellTab), independently of the main dashboard lifecycle, so
// they stay outside the global gate below.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/console/:name" element={<ConsoleWindow />} />
        <Route path="/host-shell" element={<HostShellWindow />} />
        <Route path="/container-terminal/:name" element={<ContainerTerminalWindow />} />
        <Route path="/*" element={<MainApp />} />
      </Routes>
    </BrowserRouter>
  );
}

function MainApp() {
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
    <Routes>
      <Route path="/" element={<Navigate to="/datacenter" replace />} />
      <Route path="/datacenter" element={<AppShell />} />
      <Route path="/node/:id" element={<AppShell />} />
      <Route path="/vm/:id" element={<AppShell />} />
      <Route path="*" element={<Navigate to="/datacenter" replace />} />
    </Routes>
  );
}
