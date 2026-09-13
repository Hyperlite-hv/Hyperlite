import { useEffect } from "react";
import Header from "./Header";
import ResourceTree from "./ResourceTree";
import CentralPanel from "./CentralPanel";
import TaskLogPanel from "./TaskLogPanel";
import ToastContainer from "../components/ToastContainer";
import { useInfraStore } from "../store/useInfraStore";
import { useUrlParamsToSelection, useSelectionToUrl } from "../hooks/useUrlSync";

const REFRESH_MS = 6000;

export default function AppShell() {
  const loadAll = useInfraStore((s) => s.loadAll);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const loading = useInfraStore((s) => s.loading);
  const error = useInfraStore((s) => s.error);

  useUrlParamsToSelection();
  useSelectionToUrl();

  useEffect(() => { loadAll(); }, [loadAll]);

  // Rafraichit en arriere-plan (voir refreshAll dans le store) pour que les
  // changements faits par un autre utilisateur/onglet apparaissent sans
  // recharger la page a la main. Sans effet visible pendant le chargement
  // initial (loadAll s'en charge deja) ni sur un echec transitoire.
  useEffect(() => {
    const id = setInterval(refreshAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [refreshAll]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-anthracite-900">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <div className="w-[236px] shrink-0">
          <ResourceTree />
        </div>
        <div className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-anthracite-400">Chargement de l'infrastructure...</div>
          ) : error ? (
            <div className="flex h-full items-center justify-center text-sm text-status-error">Erreur : {error}</div>
          ) : (
            <CentralPanel />
          )}
        </div>
      </div>
      <TaskLogPanel />
      <ToastContainer />
    </div>
  );
}
