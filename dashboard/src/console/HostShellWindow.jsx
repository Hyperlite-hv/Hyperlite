import { useEffect, useState } from "react";
import { Server } from "lucide-react";
import HostShellPanel from "../components/HostShellPanel";
import LoginScreen from "../auth/LoginScreen";
import { useAuthStore, selectIsAdmin } from "../store/useAuthStore";
import { fetchDashboardSummary } from "../api/client";

// Page autonome ouverte via window.open() (voir NodeShellTab.jsx), meme
// principe que ConsoleWindow.jsx pour les VM : meme origine donc meme
// session (JWT en localStorage, relu par useAuthStore).
export default function HostShellWindow() {
  const status = useAuthStore((s) => s.status);
  const isAdmin = useAuthStore(selectIsAdmin);
  const restoreSession = useAuthStore((s) => s.restoreSession);
  const [hostname, setHostname] = useState("l'hôte");

  useEffect(() => { restoreSession(); }, [restoreSession]);

  useEffect(() => {
    if (status !== "authenticated") return;
    fetchDashboardSummary().then((d) => setHostname(d.hyperviseur?.nom || "l'hôte")).catch(() => {});
  }, [status]);

  if (status === "checking") {
    return <div className="flex h-screen items-center justify-center bg-anthracite-900 text-sm text-anthracite-400">Vérification de la session...</div>;
  }
  if (status === "anonymous") {
    return <LoginScreen />;
  }
  if (!isAdmin) {
    return <div className="flex h-screen items-center justify-center bg-anthracite-900 text-sm text-anthracite-400">Shell hôte réservé au rôle admin.</div>;
  }

  return (
    <div className="flex h-screen flex-col bg-anthracite-900 p-3 gap-3">
      <div className="flex items-center gap-2 shrink-0">
        <Server size={15} className="text-accent-blue" />
        <span className="text-sm font-semibold text-anthracite-100">Shell hôte — {hostname}</span>
        <button className="btn-secondary ml-auto" onClick={() => window.close()}>Fermer la fenêtre</button>
      </div>
      <div className="flex-1 min-h-0">
        <HostShellPanel hostname={hostname} />
      </div>
    </div>
  );
}
