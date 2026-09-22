import ConfirmHost from "../components/ConfirmHost";
import { useEffect } from "react";
import Header from "./Header";
import Sidebar from "./Sidebar";
import CentralPanel from "./CentralPanel";
import TaskLogPanel from "./TaskLogPanel";
import ToastContainer from "../components/ToastContainer";
import { useInfraStore } from "../store/useInfraStore";
import { useUrlParamsToSelection, useSelectionToUrl } from "../hooks/useUrlSync";
import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

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
    <TooltipProvider delayDuration={300}>
      <SidebarProvider>
        <Sidebar />
        <SidebarInset className="overflow-hidden">
          <Header />
          <div className="flex-1 overflow-hidden">
            {loading ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading the infrastructure...</div>
            ) : error ? (
              <div className="flex h-full items-center justify-center text-sm text-destructive">Error: {error}</div>
            ) : (
              <CentralPanel />
            )}
          </div>
          <TaskLogPanel />
        </SidebarInset>
        <ToastContainer />
        <ConfirmHost />
      </SidebarProvider>
    </TooltipProvider>
  );
}
