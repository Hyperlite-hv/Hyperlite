import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppShell from "./layout/AppShell";
import LoginScreen from "./auth/LoginScreen";
import { useAuthStore } from "./store/useAuthStore";

export default function App() {
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
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/datacenter" replace />} />
        <Route path="/datacenter" element={<AppShell />} />
        <Route path="/node/:id" element={<AppShell />} />
        <Route path="/vm/:id" element={<AppShell />} />
        <Route path="*" element={<Navigate to="/datacenter" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
