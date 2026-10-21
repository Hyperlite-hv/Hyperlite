import { apiLogin, expect, test } from "../support/fixtures";

// Reading one object answers what the list gives for it, against a real libvirt (the storage pools are libvirt's).
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

test("a storage pool and the storage history are each read at their own address", async ({ request }) => {
  const pools = (await (await request.get("/storage", { headers: auth() })).json()) as { items: Array<{ name: string }> };
  const listed = pools.items.find((p) => p.name === "default");
  expect(listed).toBeTruthy();
  const one = await request.get("/storage/default", { headers: auth() });
  expect(one.status()).toBe(200);
  const read = (await one.json()) as { name: string; type: string; node: string };
  expect([read.name, read.type, read.node]).toEqual([listed!.name, (listed as { type: string }).type, (listed as { node: string }).node]);
  expect((await request.get("/storage/e2e-no-such-pool", { headers: auth() })).status()).toBe(404);
  // The metrics' route, not a pool named "history".
  const history = await request.get("/storage/history", { headers: auth() });
  expect(history.status()).toBe(200);
  expect(Object.keys(await history.json())).toContain("items");
});
