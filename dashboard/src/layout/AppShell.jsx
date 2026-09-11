import { useEffect } from "react";
import Header from "./Header";
import ResourceTree from "./ResourceTree";
import CentralPanel from "./CentralPanel";
import TaskLogPanel from "./TaskLogPanel";
import ToastContainer from "../components/ToastContainer";
import { useInfraStore } from "../store/useInfraStore";
import { useUrlParamsToSelection, useSelectionToUrl } from "../hooks/useUrlSync";

export default function AppShell() {
  const loadAll = useInfraStore((s) => s.loadAll);
  const loading = useInfraStore((s) => s.loading);
  const error = useInfraStore((s) => s.error);

  useUrlParamsToSelection();
  useSelectionToUrl();

  useEffect(() => { loadAll(); }, [loadAll]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-anthracite-900">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <div className="w-[250px] shrink-0">
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
