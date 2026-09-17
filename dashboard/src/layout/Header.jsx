import { useState } from "react";
import { Plus, Box, Bell, Sun, Moon, LogOut, RefreshCw, ChevronDown } from "lucide-react";
import SearchBar from "../components/SearchBar";
import VMWizard from "../wizard/VMWizard";
import ContainerWizard from "../wizard/ContainerWizard";
import UpdateModal from "../components/UpdateModal";
import { useInfraStore } from "../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../store/useAuthStore";

function initials(name) {
  if (!name) return "?";
  return name.slice(0, 2).toUpperCase();
}

// Fil d'Ariane "Datacenter / <selection>" (refonte 2026-09-17, calque sur la
// reference validee) -- purement indicatif, ne navigue pas (contrairement au
// selecteur de la reference qui suppose un seul niveau ; ici la vraie
// navigation reste l'arbre Datacenter, voir Sidebar.jsx/ResourceTree.jsx).
function breadcrumbLabel(selection, nodes, vms) {
  if (selection.type === "node") return nodes.find((n) => n.id === selection.id)?.nom || selection.id;
  if (selection.type === "vm") return vms.find((v) => v.nom === selection.id)?.nom || selection.id;
  if (selection.type === "storage") return selection.id;
  return null;
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
  const selection = useInfraStore((s) => s.selection);
  const nodes = useInfraStore((s) => s.nodes);
  const vms = useInfraStore((s) => s.vms);
  const username = useAuthStore((s) => s.username);
  const isAdmin = useAuthStore(selectIsAdmin);
  const logout = useAuthStore((s) => s.logout);

  const runningCount = tasks.filter((t) => t.statut === "en_cours").length;
  const recentTasks = tasks.slice(0, 5);
  const crumb = breadcrumbLabel(selection, nodes, vms);

  return (
    <header className="flex h-[60px] shrink-0 items-center gap-4 border-b border-anthracite-600 bg-anthracite-800 px-6">
      <div className="hidden shrink-0 items-center gap-1.5 text-[13px] font-bold text-anthracite-100 md:flex">
        <span>Datacenter</span>
        {crumb && (
          <>
            <span className="text-anthracite-300 font-normal">/</span>
            <span>{crumb}</span>
          </>
        )}
      </div>

      <div className="w-px h-5 bg-anthracite-600 hidden md:block shrink-0" />

      <div className="w-[240px] shrink-0">
        <SearchBar />
      </div>

      <div className="flex-1" />

      <div className="flex items-center gap-2 shrink-0">
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
          <button className="relative rounded-lg p-2.5 text-anthracite-300 hover:bg-anthracite-700 hover:text-anthracite-100" onClick={() => setNotifOpen((o) => !o)}>
            <Bell size={17} />
            {runningCount > 0 && (
              <span className="absolute top-1 right-1 flex h-4 w-4 items-center justify-center rounded-full bg-accent-orange text-[10px] font-bold text-white">
                {runningCount}
              </span>
            )}
          </button>
          {notifOpen && (
            <div className="absolute right-0 mt-1 w-72 card z-50 py-1" onMouseLeave={() => setNotifOpen(false)}>
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
            className="flex items-center gap-2 rounded-full py-1 pl-1 pr-2.5 hover:bg-anthracite-700"
            onClick={() => setUserOpen((o) => !o)}
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-accent-blue to-[#4338CA] text-xs font-bold text-white">
              {initials(username)}
            </span>
            <span className="hidden text-[12.5px] font-semibold text-anthracite-100 sm:inline">{username}</span>
            <ChevronDown size={13} className="hidden text-anthracite-400 sm:inline" />
          </button>
          {userOpen && (
            <div className="absolute right-0 mt-1 w-48 card z-50 py-1" onMouseLeave={() => setUserOpen(false)}>
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
