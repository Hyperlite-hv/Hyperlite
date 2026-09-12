import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Server } from "lucide-react";
import ConsolePanel from "../components/ConsolePanel";
import LoginScreen from "../auth/LoginScreen";
import { useAuthStore } from "../store/useAuthStore";
import { fetchVM } from "../api/client";

// Page autonome (pas d'AppShell, pas de sidebar/header) ouverte dans une
// fenetre/onglet separe via window.open() -- voir VMConsoleTab.jsx. Meme
// origine que le reste de l'app donc meme session (JWT en localStorage,
// relu par useAuthStore comme partout ailleurs).
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
    return <div className="flex h-screen items-center justify-center bg-anthracite-900 text-sm text-anthracite-400">Verification de la session...</div>;
  }
  if (status === "anonymous") {
    return <LoginScreen />;
  }

  return (
    <div className="flex h-screen flex-col bg-anthracite-900 p-3 gap-3">
      <div className="flex items-center gap-2 shrink-0">
        <Server size={15} className="text-accent-blue" />
        <span className="text-sm font-semibold text-anthracite-100">{name}</span>
        {vm?.etat && <span className="text-xs text-anthracite-400">({vm.etat})</span>}
        <button className="btn-secondary ml-auto" onClick={() => window.close()}>Fermer la fenetre</button>
      </div>
      {error && <p className="text-xs text-status-error">{error}</p>}
      <div className="flex-1 min-h-0">
        <ConsolePanel vmName={name} vmActive={vm?.etat === "actif"} initialMode={initialMode} />
      </div>
    </div>
  );
}
