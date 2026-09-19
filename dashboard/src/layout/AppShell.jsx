import { useEffect } from "react";
import Header from "./Header";
import Sidebar from "./Sidebar";
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

  // Refreshes in the background (see refreshAll in the store) so changes made by
  // another user/tab appear without reloading the page by hand. No visible effect
  // during the initial load (loadAll already handles it) nor on a transient
  // failure.
  useEffect(() => {
    const id = setInterval(refreshAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [refreshAll]);

  return (
    <div className="flex h-screen overflow-hidden bg-anthracite-900">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <div className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-anthracite-400">Loading the infrastructure...</div>
          ) : error ? (
            <div className="flex h-full items-center justify-center text-sm text-status-error">Error: {error}</div>
          ) : (
            <CentralPanel />
          )}
        </div>
        {/* Real bug found by eyeballing the UI (Playwright): placed as a direct sibling of the Sidebar+content column (root container in flex-row), TaskLogPanel rendered as a narrow column on the right of the whole screen instead of a bar at the bottom. Kept inside the content column (below Header+CentralPanel). */}
        <TaskLogPanel />
      </div>
      <ToastContainer />
    </div>
  );
}
