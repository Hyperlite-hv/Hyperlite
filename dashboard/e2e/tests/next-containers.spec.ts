import type { Page } from "@playwright/test";
import { expect, test, ADMIN } from "../support/fixtures";

// Containers page. LXC needs a debootstrapped image the throwaway test host does not have, so the
// /containers API is replaced by a small stateful stand-in: this checks the page's behaviour
// (payloads, confirmations, gating), not LXC itself.
type Ct = { nom: string; etat: string; vcpu: number; memoire_mo: number; ip: string | null };
test.describe.configure({ mode: "serial", timeout: 90_000 });

async function mockApi(page: Page) {
  const cts: Ct[] = [{ nom: "web1", etat: "actif", vcpu: 1, memoire_mo: 512, ip: "192.168.122.50" }, { nom: "db1", etat: "arrete", vcpu: 2, memoire_mo: 1024, ip: null }];
  const calls: string[] = [];
  let created: unknown = null;
  await page.route(/\/containers(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request();
    if (!req.url().includes("/containers") || req.resourceType() === "document") return route.fallback();
    const path = new URL(req.url()).pathname.replace(/^.*\/containers/, "");
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (req.method() === "GET" && path === "") return json(cts);
    if (req.method() === "GET" && path.startsWith("/backups")) return json([]);
    if (req.method() === "POST" && path === "") { created = req.postDataJSON(); cts.push({ nom: (created as { name: string }).name, etat: "arrete", vcpu: 1, memoire_mo: 512, ip: null }); return json({ ok: true }); }
    const m = path.match(/^\/([^/]+)\/(start|stop)$/);
    if (req.method() === "POST" && m) { calls.push(`${m[2]} ${m[1]}`); cts.find((c) => c.nom === m[1])!.etat = m[2] === "start" ? "actif" : "arrete"; return json({ ok: true }); }
    if (req.method() === "DELETE") { calls.push(`delete ${path.slice(1).split("?")[0]}`); const i = cts.findIndex((c) => c.nom === path.slice(1).split("?")[0]); if (i >= 0) cts.splice(i, 1); return json({ ok: true }); }
    return route.fallback();
  });
  return { calls, created: () => created };
}

async function open(page: Page) {
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  const api = await mockApi(page);
  await page.goto("/");
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=containers");
  return api;
}

test("lists containers with state text, gates stop/delete behind confirmations, and creates from the gallery", async ({ page }) => {
  const api = await open(page);
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: /^Containers/ })).toBeVisible();
  await expect(main.getByRole("row", { name: /web1/ })).toContainText("192.168.122.50");
  await expect(main.getByRole("row", { name: /db1/ })).toContainText("Not assigned");

  await main.getByRole("button", { name: "Stop web1" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(api.calls).toEqual([]);
  await main.getByRole("button", { name: "Stop web1" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(() => api.calls).toContain("stop web1");

  await main.getByRole("button", { name: "Start db1" }).click();
  await expect.poll(() => api.calls).toContain("start db1");

  await main.getByRole("button", { name: "Delete container db1" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("filesystem included");
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(main.getByRole("row", { name: /db1/ })).toHaveCount(0, { timeout: 15_000 });

  await main.getByRole("button", { name: "Create a container" }).click();
  const submit = main.getByRole("button", { name: "Create", exact: true });
  await expect(submit).toBeDisabled();
  await main.getByRole("button", { name: /Alpine/ }).click();
  await expect(main.getByRole("button", { name: /Alpine/ })).toHaveAttribute("aria-pressed", "true");
  await main.getByLabel("Name", { exact: true }).fill("cache1");
  await main.getByLabel("User", { exact: true }).fill("ops");
  await main.getByLabel("Password", { exact: true }).fill("Correct-Horse-1");
  await submit.click();
  await expect(main.getByRole("row", { name: /cache1/ })).toBeVisible({ timeout: 15_000 });
  expect(api.created()).toMatchObject({ name: "cache1", image: "alpine:3.19", username: "ops", vcpu: 1, memory_mb: 512 });
});

test("French labels and no horizontal overflow at a narrow width", async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 800 });
  await page.addInitScript(() => { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "fr"); });
  await mockApi(page);
  await page.goto("/");
  await page.getByLabel(/Nom d.utilisateur|Username/).fill(ADMIN.username);
  await page.getByLabel(/Mot de passe|Password/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Se connecter|Sign in/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=containers");
  const main = page.getByRole("main");
  await expect(main.getByRole("button", { name: "Créer un conteneur" })).toBeVisible();
  await main.getByRole("button", { name: "Créer un conteneur" }).click();
  await expect(main.getByText("Distribution généraliste")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
