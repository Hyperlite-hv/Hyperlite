import { useState } from "react";
import { Server, Plus, Bell, Sun, Moon, User, Box } from "lucide-react";
import SearchBar from "../components/SearchBar";
import VMWizard from "../wizard/VMWizard";
import { useInfraStore } from "../store/useInfraStore";

export default function Header() {
  const [wizardOpen, setWizardOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const theme = useInfraStore((s) => s.theme);
  const toggleTheme = useInfraStore((s) => s.toggleTheme);
  const tasks = useInfraStore((s) => s.tasks);
  const pushToast = useInfraStore((s) => s.pushToast);

  const runningCount = tasks.filter((t) => t.statut === "en_cours").length;
  const recentTasks = tasks.slice(0, 5);

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-anthracite-600 bg-anthracite-800 px-4">
      <div className="flex items-center gap-2 shrink-0">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-accent-blue">
          <Server size={16} className="text-white" />
        </div>
        <span className="text-sm font-semibold tracking-wide text-anthracite-100">HYPERLITE</span>
      </div>

      <div className="flex-1 flex justify-center">
        <SearchBar />
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <button className="btn-primary" onClick={() => setWizardOpen(true)}>
          <Plus size={15} /> Creer VM
        </button>
        <button
          className="btn-secondary"
          onClick={() => pushToast({ kind: "error", title: "Non disponible", message: "Les conteneurs LXC ne sont pas geres par le backend Hyperlite pour le moment." })}
        >
          <Box size={15} /> Creer conteneur
        </button>

        <div className="relative">
          <button className="relative rounded-md p-2 text-anthracite-300 hover:bg-anthracite-700 hover:text-anthracite-100" onClick={() => setNotifOpen((o) => !o)}>
            <Bell size={17} />
            {runningCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-accent-orange text-[10px] font-bold text-white">
                {runningCount}
              </span>
            )}
          </button>
          {notifOpen && (
            <div className="absolute right-0 mt-1 w-72 card border border-anthracite-600 z-50 py-1" onMouseLeave={() => setNotifOpen(false)}>
              <div className="px-3 py-1.5 text-xs font-semibold text-anthracite-300">Taches recentes</div>
              {recentTasks.length === 0 && <div className="px-3 py-2 text-sm text-anthracite-400">Aucune tache.</div>}
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
          <button className="rounded-md p-2 text-anthracite-300 hover:bg-anthracite-700 hover:text-anthracite-100" onClick={() => setUserOpen((o) => !o)}>
            <User size={17} />
          </button>
          {userOpen && (
            <div className="absolute right-0 mt-1 w-48 card border border-anthracite-600 z-50 py-1" onMouseLeave={() => setUserOpen(false)}>
              <div className="px-3 py-1.5 text-sm text-anthracite-100">admin</div>
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-anthracite-200 hover:bg-anthracite-700"
                onClick={toggleTheme}
              >
                {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
                {theme === "dark" ? "Mode clair" : "Mode sombre"}
              </button>
            </div>
          )}
        </div>
      </div>

      <VMWizard open={wizardOpen} onClose={() => setWizardOpen(false)} />
    </header>
  );
}
