import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppShell from "./layout/AppShell";
import LoginScreen from "./auth/LoginScreen";
import ConsoleWindow from "./console/ConsoleWindow";
import { useAuthStore } from "./store/useAuthStore";

// /console/:name a son propre gate d'authentification (ConsoleWindow) : cette
// page s'ouvre dans une fenetre separee (voir VMConsoleTab), independamment
// du cycle de vie du tableau de bord principal, donc elle reste en dehors du
// gate global ci-dessous.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/console/:name" element={<ConsoleWindow />} />
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
    return <div className="flex h-screen items-center justify-center bg-anthracite-900 text-sm text-anthracite-400">Verification de la session...</div>;
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
