import { afterEach, describe, expect, it, vi } from "vitest";

// The API is replaced by controllable promises: the test decides when each answer arrives.
const pending = [];
vi.mock("../api/client", () => {
  const answer = () => new Promise((resolve) => pending.push(resolve));
  return {
    fetchNodes: () => answer(), fetchVMs: () => answer(), fetchStoragePools: () => answer(), fetchNetworks: () => answer(),
    fetchContainers: vi.fn(), fetchPools: vi.fn(),
    setAuthToken: vi.fn(), setUnauthorizedHandler: vi.fn(),
  };
});

const { refreshInventory } = await import("../next/lib/inventory");
const { useInfraStore } = await import("../store/useInfraStore");

function answerAll(vms) {
  const batch = pending.splice(0, 4);
  batch[0]([]); batch[1](vms); batch[2]([]); batch[3]([]);
}

afterEach(() => { pending.length = 0; useInfraStore.setState({ vms: [], mutations: 0 }); });

describe("inventory refresh", () => {
  it("drops an answer that started before a local change", async () => {
    useInfraStore.setState({ vms: [{ nom: "web", node: "local", etat: "actif" }], mutations: 0 });
    const refresh = refreshInventory();
    // The VM is stopped while the refresh is on its way; its answer still says "actif".
    useInfraStore.setState((s) => ({ mutations: s.mutations + 1, vms: [{ nom: "web", node: "local", etat: "arrete" }] }));
    answerAll([{ nom: "web", node: "local", etat: "actif" }]);
    await refresh;
    expect(useInfraStore.getState().vms[0].etat).toBe("arrete");
  });

  it("keeps only the latest of two overlapping refreshes", async () => {
    const older = refreshInventory();
    const newer = refreshInventory();
    const olderBatch = pending.splice(0, 4);
    answerAll([{ nom: "new", node: "local", etat: "actif" }]);
    await newer;
    olderBatch[0]([]); olderBatch[1]([{ nom: "old", node: "local", etat: "actif" }]); olderBatch[2]([]); olderBatch[3]([]);
    await older;
    expect(useInfraStore.getState().vms.map((v) => v.nom)).toEqual(["new"]);
  });
});
