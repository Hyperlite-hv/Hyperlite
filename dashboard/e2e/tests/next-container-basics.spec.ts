import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Basic container actions: the variables an image requires (asked for, with a generated password), renaming a
// stopped container, and the root shell of a running one. The container list is served by the test (the test host
// has no LXC); the image requirements come from the real backend (GET /containers/image-env).
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
test.describe.configure({ mode: "serial", timeout: 90_000 });

type Ct = { nom: string; etat: string; mode: string; image: string | null; vcpu: number; memoire_mo: number; ip: string | null };

async function serve(page: import("@playwright/test").Page) {
  const cts: Ct[] = [
    { nom: "e2e-basics-web", etat: "actif", mode: "application", image: "nginx:latest", vcpu: 1, memoire_mo: 512, ip: "192.168.122.60" },
    { nom: "e2e-basics-db", etat: "arrete", mode: "application", image: "postgres:16", vcpu: 1, memoire_mo: 512, ip: null },
  ];
  const seen = { created: null as Record<string, unknown> | null, renames: [] as string[] };
  await page.route(/\/containers(\/[^/?]+(\/[a-z-]+)?)?(\?.*)?$/, async (r) => {
    if (!api(r)) return r.fallback();
    const req = r.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/containers/, "");
    if (path === "/image-env" || path === "/docker-hub/search") return r.fallback();
    if (req.method() === "GET" && path === "") return r.fulfill(json(cts));
    if (req.method() === "GET" && path === "/backups") return r.fulfill(json([]));
    if (req.method() === "POST" && path === "") {
      seen.created = req.postDataJSON();
      cts.push({ nom: String(seen.created!.name), etat: "actif", mode: "application", image: String(seen.created!.image), vcpu: 1, memoire_mo: 512, ip: "192.168.122.61" });
      return r.fulfill(json(cts[cts.length - 1], 201));
    }
    const m = path.match(/^\/([^/]+)\/rename$/);
    if (req.method() === "POST" && m) {
      const ct = cts.find((c) => c.nom === m[1])!;
      seen.renames.push(`${m[1]} -> ${req.postDataJSON().new_name}`);
      ct.nom = req.postDataJSON().new_name;
      return r.fulfill(json(ct));
    }
    return r.fallback();
  });
  await page.route(/\/networks(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET"
    ? r.fulfill(json([{ nom: "default", type: "nat", pont: "virbr0", actif: true, reseau: "192.168.122.0/24", autostart: true, dhcp: true, vms: [] }]))
    : r.fallback()));
  return seen;
}

test("postgres asks for its password, generated and changeable, and a variable typed below replaces it", async ({ page }) => {
  await uiLogin(page);
  const seen = await serve(page);
  await page.goto("/datacenter?tab=containers");
  const main = page.getByRole("main");
  await main.getByRole("button", { name: "Create a container" }).click();
  const dlg = page.getByRole("dialog", { name: "Create a container" });
  await dlg.getByRole("button", { name: /^PostgreSQL/ }).click();
  const required = dlg.getByRole("group", { name: "Variables the image requires" });
  const password = required.getByLabel("POSTGRES_PASSWORD");
  await expect(password).toHaveValue(/^[A-Za-z0-9]{20}$/);
  await expect(required).toContainText("postgres:16 does not start without them.");

  await password.fill("");
  await dlg.getByLabel("Name", { exact: true }).fill("e2e-basics-pg");
  await dlg.getByRole("button", { name: "Create the container", exact: true }).click();
  await expect(required.getByText("The image requires this variable.")).toBeVisible();
  expect(seen.created).toBeNull();

  await required.getByRole("button", { name: "Generate" }).click();
  const generated = await password.inputValue();
  expect(generated).toMatch(/^[A-Za-z0-9]{20}$/);
  await dlg.getByRole("button", { name: "+POSTGRES_DB" }).click();
  await expect(dlg.getByLabel(/^Environment variables/)).toHaveValue("POSTGRES_DB=");
  await dlg.getByLabel(/^Environment variables/).fill("POSTGRES_DB=shop");
  await dlg.getByRole("button", { name: "Create the container", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
  expect(seen.created).toMatchObject({ name: "e2e-basics-pg", image: "postgres:16", env: { POSTGRES_PASSWORD: generated, POSTGRES_DB: "shop" } });

  // The alternative typed in the box meets the requirement: the field steps aside and is not sent.
  await main.getByRole("button", { name: "Create a container" }).click();
  await dlg.getByRole("button", { name: /^PostgreSQL/ }).click();
  await expect(required.getByLabel("POSTGRES_PASSWORD")).toBeVisible();
  await dlg.getByLabel(/^Environment variables/).fill("POSTGRES_PASSWORD=mine");
  await expect(required.getByLabel("POSTGRES_PASSWORD")).toHaveCount(0);
  await dlg.getByLabel("Name", { exact: true }).fill("e2e-basics-pg2");
  await dlg.getByRole("button", { name: "Create the container", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
  expect(seen.created!.env).toEqual({ POSTGRES_PASSWORD: "mine" });

  // An image with no requirement shows no such section.
  await main.getByRole("button", { name: "Create a container" }).click();
  await dlg.getByRole("button", { name: /^Redis/ }).click();
  await expect(dlg.getByRole("group", { name: "Variables the image requires" })).toHaveCount(0);
});

test("a stopped container is renamed from its menu, a running one opens a root shell", async ({ page }) => {
  await uiLogin(page);
  const seen = await serve(page);
  await page.goto("/datacenter?tab=containers");
  const main = page.getByRole("main");

  await main.getByRole("row", { name: /e2e-basics-web/ }).click({ button: "right" });
  const running = page.getByRole("menu");
  await expect(running.getByRole("menuitem", { name: /Rename/ })).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");

  await main.getByRole("row", { name: /e2e-basics-db/ }).click({ button: "right" });
  await page.getByRole("menu").getByRole("menuitem", { name: /Rename/ }).click();
  const prompt = page.getByRole("dialog");
  await prompt.getByLabel("New name").fill("bad name");
  await prompt.getByRole("button", { name: "Rename" }).click();
  await expect(prompt.getByRole("alert")).toBeVisible();
  await prompt.getByLabel("New name").fill("e2e-basics-shop");
  await prompt.getByRole("button", { name: "Rename" }).click();
  await expect(main.getByRole("row", { name: /e2e-basics-shop/ })).toBeVisible({ timeout: 15_000 });
  expect(seen.renames).toEqual(["e2e-basics-db -> e2e-basics-shop"]);

  const [popup] = await Promise.all([
    page.context().waitForEvent("page"),
    main.getByRole("button", { name: "Root shell in e2e-basics-web" }).click(),
  ]);
  expect(popup.url()).toContain("/container-terminal/e2e-basics-web?shell=1");
});
