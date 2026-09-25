import { create } from "zustand";
import { fetchNodes, fetchVMs, fetchStoragePools, fetchNetworks, fetchContainers, fetchPools } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";

// Freshness of the inventory data, so the UI can say when it is stale instead of silently
// showing old data (the legacy refresh swallows every failure).
export const useFreshness = create((set) => ({
  updatedAt: null, failing: false, lastError: null, loaded: false,
  containers: [], pools: null, poolsState: "idle", // idle | ok | forbidden | error
  markOk() { set({ updatedAt: Date.now(), failing: false, lastError: null, loaded: true }); },
  markFail(e) { set({ failing: true, lastError: e?.message || String(e) }); },
  setExtras(patch) { set(patch); },
}));

// Same API functions and contracts as the legacy store; only the orchestration is new.
export async function refreshInventory({ initial = false } = {}) {
  const infra = useInfraStore;
  if (initial) infra.setState({ loading: true, error: null });
  try {
    const [nodes, vms, storagePools, networks] = await Promise.all([fetchNodes(), fetchVMs(), fetchStoragePools(), fetchNetworks()]);
    infra.setState({ nodes, vms, storagePools, networks, loading: false, error: null });
    useFreshness.getState().markOk();
  } catch (e) {
    useFreshness.getState().markFail(e);
    if (initial) infra.setState({ loading: false, error: e.message });
    throw e;
  }
}

export async function refreshExtras() {
  const f = useFreshness.getState();
  try {
    const containers = await fetchContainers();
    f.setExtras({ containers: Array.isArray(containers) ? containers : [] });
  } catch { /* containers are optional: keep the last list */ }
  // Resource pools are administrator-only on the backend: do not ask (and collect 403s) for anyone else.
  if (useAuthStore.getState().role !== "admin") { f.setExtras({ pools: null, poolsState: "forbidden" }); return; }
  try {
    const pools = await fetchPools();
    f.setExtras({ pools: Array.isArray(pools) ? pools : [], poolsState: "ok" });
  } catch (e) {
    const forbidden = /403|administrator|permission|forbidden|not allowed|role/i.test(e?.message || "");
    f.setExtras({ pools: null, poolsState: forbidden ? "forbidden" : "error" });
  }
}
