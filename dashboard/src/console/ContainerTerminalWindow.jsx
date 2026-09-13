import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { Box } from "lucide-react";
import ContainerShellPanel from "../components/ContainerShellPanel";
import LoginScreen from "../auth/LoginScreen";
import { useAuthStore } from "../store/useAuthStore";

// Page autonome ouverte via window.open() (voir ContainersTab.jsx), meme
// principe que ConsoleWindow.jsx / HostShellWindow.jsx : meme origine donc
// meme session (JWT en localStorage, relu par useAuthStore).
export default function ContainerTerminalWindow() {
  const { name } = useParams();
  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);

  useEffect(() => { restoreSession(); }, [restoreSession]);

  if (status === "checking") {
    return <div className="flex h-screen items-center justify-center bg-anthracite-900 text-sm text-anthracite-400">Vérification de la session...</div>;
  }
  if (status === "anonymous") {
    return <LoginScreen />;
  }

  return (
    <div className="flex h-screen flex-col bg-anthracite-900 p-3 gap-3">
      <div className="flex items-center gap-2 shrink-0">
        <Box size={15} className="text-accent-blue" />
        <span className="text-sm font-semibold text-anthracite-100">Terminal — {name}</span>
        <button className="btn-secondary ml-auto" onClick={() => window.close()}>Fermer la fenêtre</button>
      </div>
      <div className="flex-1 min-h-0">
        <ContainerShellPanel name={name} />
      </div>
    </div>
  );
}
