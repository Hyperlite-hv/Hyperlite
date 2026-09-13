import { useState } from "react";
import { Plus, Box, Bell, Sun, Moon, LogOut, RefreshCw } from "lucide-react";
import SearchBar from "../components/SearchBar";
import HyperliteLogo from "../components/HyperliteLogo";
import VMWizard from "../wizard/VMWizard";
import ContainerWizard from "../wizard/ContainerWizard";
import UpdateModal from "../components/UpdateModal";
import { useInfraStore } from "../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../store/useAuthStore";

function initials(name) {
  if (!name) return "?";
  return name.slice(0, 2).toUpperCase();
}

export default function Header() {
  const [wizardOpen, setWizardOpen] = useState(false);
  const [containerWizardOpen, setContainerWizardOpen] = useState(false);
  const [updateOpen, setUpdateOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const theme = useInfraStore((s) => s.theme);
  const toggleTheme = useInfraStore((s) => s.toggleTheme);
  const tasks = useInfraStore((s) => s.tasks);
  const username = useAuthStore((s) => s.username);
  const isAdmin = useAuthStore(selectIsAdmin);
  const logout = useAuthStore((s) => s.logout);

  const runningCount = tasks.filter((t) => t.statut === "en_cours").length;
  const recentTasks = tasks.slice(0, 5);

  return (
    <header className="flex h-14 shrink-0 items-center gap-5 border-b border-chrome-950 bg-chrome-900 px-4">
      <div className="flex items-center gap-2 shrink-0">
        <HyperliteLogo size={26} />
        <span className="text-sm font-extrabold tracking-wide text-chrome-100">HYPERLITE</span>
      </div>

      <div className="flex-1" />

      <div className="flex items-center gap-2 shrink-0">
        <div className="w-[220px]">
          <SearchBar />
        </div>

        {isAdmin && (
          <button className="btn-primary !rounded-full" onClick={() => setWizardOpen(true)}>
            <Plus size={15} /> Créer VM
          </button>
        )}
        {isAdmin && (
          <button className="btn-secondary" onClick={() => setContainerWizardOpen(true)}>
            <Box size={15} /> Créer conteneur
          </button>
        )}

        <div className="relative">
          <button className="relative rounded-md p-2 text-chrome-400 hover:bg-chrome-700 hover:text-chrome-100" onClick={() => setNotifOpen((o) => !o)}>
            <Bell size={17} />
            {runningCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-accent-orange text-[10px] font-bold text-white">
                {runningCount}
              </span>
            )}
          </button>
          {notifOpen && (
            <div className="absolute right-0 mt-1 w-72 card border border-anthracite-600 z-50 py-1" onMouseLeave={() => setNotifOpen(false)}>
              <div className="px-3 py-1.5 text-xs font-semibold text-anthracite-300">Tâches récentes</div>
              {recentTasks.length === 0 && <div className="px-3 py-2 text-sm text-anthracite-400">Aucune tâche.</div>}
              {recentTasks.map((t) => (
                <div key={t.id} className="px-3 py-1.5 text-sm">
                  <div className="flex justify-between text-anthracite-100">
                    <span>{t.type}</span>
                    <span className="text-xs text-anthracite-400">{t.cible}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="relative">
          <button
            className="flex h-8 w-8 items-center justify-center rounded-full bg-anthracite-700 border border-anthracite-500 text-xs font-semibold text-anthracite-100"
            onClick={() => setUserOpen((o) => !o)}
          >
            {initials(username)}
          </button>
          {userOpen && (
            <div className="absolute right-0 mt-1 w-48 card border border-anthracite-600 z-50 py-1" onMouseLeave={() => setUserOpen(false)}>
              <div className="px-3 py-1.5 text-sm text-anthracite-100">{username} <span className="text-xs text-anthracite-400">({isAdmin ? "admin" : "observateur"})</span></div>
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-anthracite-200 hover:bg-anthracite-700"
                onClick={toggleTheme}
              >
                {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
                {theme === "dark" ? "Mode clair" : "Mode sombre"}
              </button>
              {isAdmin && (
                <button
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-anthracite-200 hover:bg-anthracite-700"
                  onClick={() => { setUpdateOpen(true); setUserOpen(false); }}
                >
                  <RefreshCw size={14} /> Vérifier les mises à jour
                </button>
              )}
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-anthracite-200 hover:bg-anthracite-700"
                onClick={logout}
              >
                <LogOut size={14} /> Se déconnecter
              </button>
            </div>
          )}
        </div>
      </div>

      <VMWizard open={wizardOpen} onClose={() => setWizardOpen(false)} />
      <ContainerWizard open={containerWizardOpen} onClose={() => setContainerWizardOpen(false)} />
      {updateOpen && <UpdateModal onClose={() => setUpdateOpen(false)} />}
    </header>
  );
}
