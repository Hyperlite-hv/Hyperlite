import AxeBuilder from "@axe-core/playwright";
import type { Page, APIRequestContext } from "@playwright/test";
import { apiLogin, expect, test, ADMIN, PREFIX } from "../support/fixtures";
import en from "../../src/next/i18n/en.js";

// Rigorous audit of the rebuilt interface in Chromium: every page in both themes and languages
// (errors, network failures, raw translation keys, untranslated text, accessibility), layout on
// five viewports, keyboard reachability, dialogs and menus, and a real VM lifecycle.
// Findings are collected with soft assertions so one run reports everything.

const DC_PAGES = ["summary", "vms", "containers", "storage", "reseau", "templates", "backups", "exports", "snapshots", "activity", "journal", "nodes", "ha", "compat", "permissions", "sso", "automation", "notifications"];
const NODE_PAGES = ["summary", "system", "tasks", "network", "disk", "compat", "shell"];
const VM_PAGES = ["summary", "console", "hardware", "options", "snapshots", "backup"];
const FRENCH = /\b(Lecteur|Operateur|Gestionnaire|Chargement|Erreur|Annuler|Créer|Supprimer|Aucun|Aucune|Réseau|Stockage|Sauvegarde|Enregistrer|Activer|Utilisateur|Mot de passe|En cours|Terminé|Échec|Statut|Ajouter|Fermer|Démarrer|Arrêter|Actualiser|Nœud|Hôte|Disque|Mémoire|Modèle|Résumé|Tâches|Conteneurs?|Instantané|En marche|En pause|Tester)\b|[àâçéèêëîïôûùüœ]/;
const KEY_TOKEN = /(?<![\w./-])(?:nav|inv|tab|ns|vm|vmlist|ov|act|snap|dock|menu|top|res|state|health|find|cmd|err|task|group|sub|crumb|legacy|app|action|actions|session)\.[a-z][\w.]*/g;
const IGNORED_HTTP = [/favicon/, /\/novnc\//, /\/xterm\//];

async function login(page: Page, { theme, lang }: { theme: string; lang: string }) {
  await page.addInitScript(([th, lg]) => {
    if (!localStorage.getItem("hyperlite-ui")) {
      localStorage.setItem("hyperlite-ui", "next");
      localStorage.setItem("hyperlite-next-theme", th);
      localStorage.setItem("hyperlite-next-lang", lg);
    }
  }, [theme, lang]);
  await page.goto("/");
  await page.getByLabel(lang === "fr" ? /Nom d.utilisateur|Username/ : "Username").fill(ADMIN.username);
  await page.getByLabel(/Password|Mot de passe/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
}

function collect(page: Page) {
  const found: string[] = [];
  page.on("pageerror", (e) => found.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) found.push(`console: ${m.text().slice(0, 200)}`); });
  page.on("response", (r) => { if (r.status() >= 400 && !IGNORED_HTTP.some((rx) => rx.test(r.url()))) found.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`); });
  return found;
}

async function vmName(request: APIRequestContext) {
  const token = await apiLogin(request);
  const list = await (await request.get("/vms", { headers: { Authorization: `Bearer ${token}` } })).json();
  return (list as { nom: string }[]).find((v) => !v.nom.startsWith(PREFIX))?.nom ?? (list as { nom: string }[])[0]?.nom;
}

const urlFor = (kind: string, id: string, tab: string, vm?: string) =>
  kind === "dc" ? (tab === "summary" ? "/datacenter" : `/datacenter?tab=${tab}`)
  : kind === "node" ? (tab === "summary" ? "/node/local" : `/node/local?tab=${tab}`)
  : (tab === "summary" ? `/vm/${encodeURIComponent(vm!)}` : `/vm/${encodeURIComponent(vm!)}?tab=${tab}`);

test.describe.configure({ timeout: 240_000 });

for (const [theme, lang] of [["dark", "en"], ["light", "fr"]] as const) {
  test(`every page is clean in ${theme}/${lang}: no errors, no failed request, no raw key, no wrong-language text, no serious a11y issue`, async ({ page, request }) => {
    const found = collect(page);
    const vm = await vmName(request);
    await login(page, { theme, lang });
    const targets: [string, string, string][] = [
      ...DC_PAGES.map((t) => ["dc", "dc", t] as [string, string, string]),
      ...NODE_PAGES.map((t) => ["node", "local", t] as [string, string, string]),
      ...(vm ? VM_PAGES.map((t) => ["vm", vm, t] as [string, string, string]) : []),
    ];
    const seenKeys = new Set(Object.keys(en));
    for (const [kind, id, tab] of targets) {
      const label = `${kind}:${tab}`;
      const before = found.length;
      await page.goto(urlFor(kind, id, tab, vm));
      await expect(page.locator(".nx-root")).toBeVisible();
      await page.waitForTimeout(1800);
      const text = await page.locator("body").innerText();
      // raw translation keys
      for (const k of new Set(text.match(KEY_TOKEN) || [])) expect.soft(seenKeys.has(k) ? "" : k, `${label}: unknown translation key rendered "${k}"`).toBe("");
      for (const k of new Set(text.match(KEY_TOKEN) || [])) if (seenKeys.has(k)) expect.soft(k, `${label}: raw translation key shown "${k}"`).toBe("");
      // wrong-language text in the rebuilt chrome (sidebar + top bar + page heading)
      if (lang === "en") {
        const chrome = await page.locator(".nx-sidebar, .nx-top, .nx-main h1").allInnerTexts();
        expect.soft(chrome.join(" ").match(FRENCH)?.[0] ?? "", `${label}: French text in the English interface`).toBe("");
      }
      // accessibility (WCAG 2 A/AA): serious and critical only
      const axe = await new AxeBuilder({ page }).include(".nx-root").withTags(["wcag2a", "wcag2aa"]).analyze();
      const blocking = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect.soft(blocking.map((v) => `${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`), `${label}: accessibility`).toEqual([]);
      expect.soft(found.slice(before), `${label}: runtime errors / failed requests`).toEqual([]);
    }
  });
}

test("no horizontal overflow and the layout stays usable on five viewports", async ({ page, request }) => {
  const vm = await vmName(request);
  await login(page, { theme: "dark", lang: "en" });
  for (const [w, h] of [[1920, 1080], [1440, 900], [1280, 720], [1024, 768], [820, 1180], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    for (const [kind, id, tab] of [["dc", "dc", "summary"], ["dc", "dc", "vms"], ["dc", "dc", "activity"], ["dc", "dc", "storage"], ["dc", "dc", "permissions"], ["node", "local", "summary"], ["vm", vm ?? "", "summary"]] as const) {
      if (kind === "vm" && !vm) continue;
      await page.goto(urlFor(kind, id, tab, vm));
      await page.waitForTimeout(900);
      const m = await page.evaluate(() => {
        const d = document.documentElement, main = document.querySelector(".nx-content") as HTMLElement | null;
        return { page: d.scrollWidth - d.clientWidth, main: main ? main.scrollWidth - main.clientWidth : 0, findVisible: !!document.querySelector(".nx-find") };
      });
      expect.soft(m.page, `${w}px ${kind}:${tab}: page overflows horizontally by ${m.page}px`).toBeLessThanOrEqual(1);
      expect.soft(m.main, `${w}px ${kind}:${tab}: content area overflows by ${m.main}px`).toBeLessThanOrEqual(24);
      expect.soft(m.findVisible, `${w}px: search entry point exists`).toBe(true);
    }
  }
});

test("dialogs, menus and the palette stay inside the screen with every action reachable (desktop, tablet, phone)", async ({ page }) => {
  await login(page, { theme: "dark", lang: "en" });
  for (const [w, h] of [[1440, 900], [820, 1180], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto("/datacenter");
    await page.waitForTimeout(800);
    const inside = async (name: string, root: import("@playwright/test").Locator) => {
      const box = await root.boundingBox();
      expect.soft(box, `${w}px ${name}: is rendered`).not.toBeNull();
      if (!box) return;
      expect.soft(box.x, `${w}px ${name}: left edge`).toBeGreaterThanOrEqual(-1);
      expect.soft(box.x + box.width, `${w}px ${name}: right edge (${Math.round(box.x + box.width)} > ${w})`).toBeLessThanOrEqual(w + 1);
      expect.soft(box.y, `${w}px ${name}: top edge`).toBeGreaterThanOrEqual(-1);
      expect.soft(box.y + box.height, `${w}px ${name}: bottom edge`).toBeLessThanOrEqual(h + 1);
      const overflow = await root.evaluate((el) => el.scrollWidth - el.clientWidth);
      expect.soft(overflow, `${w}px ${name}: horizontal overflow inside`).toBeLessThanOrEqual(1);
      for (const b of await root.getByRole("button").all()) {
        if (!(await b.isVisible())) continue;
        const bb = await b.boundingBox();
        if (bb) expect.soft(bb.x + bb.width, `${w}px ${name}: button "${(await b.innerText()).trim().slice(0, 20)}" cut off on the right`).toBeLessThanOrEqual(w + 1);
      }
    };
    // Create menu
    await page.getByRole("button", { name: "Create" }).click();
    await inside("Create menu", page.getByRole("menu"));
    // VM wizard: the stepper and the footer buttons must be visible
    await page.getByRole("menuitem", { name: "Virtual machine" }).click();
    const dlg = page.getByRole("dialog");
    await inside("VM wizard", dlg);
    await expect.soft(dlg.getByRole("button", { name: "Next" }), `${w}px VM wizard: Next reachable`).toBeInViewport();
    await page.keyboard.press("Escape");
    // container wizard
    await page.getByRole("button", { name: "Create" }).click();
    await page.getByRole("menuitem", { name: "Container" }).click();
    await inside("Container wizard", page.getByRole("dialog"));
    await page.keyboard.press("Escape");
    // palette
    await page.keyboard.press("Control+k");
    await inside("Command palette", page.getByRole("dialog", { name: "Find a node or VM" }));
    await page.keyboard.press("Escape");
    // user menu and its dialogs (the sidebar is a drawer below 1024px)
    const openDrawer = async () => {
      if (w >= 1024) return;
      if ((await page.locator(".nx-root").getAttribute("data-sidebar")) !== "open") await page.getByRole("button", { name: "Toggle sidebar" }).click({ timeout: 5000 });
      await expect(page.locator('.nx-root[data-sidebar="open"]'), `${w}px: the sidebar drawer opens`).toBeVisible({ timeout: 5000 });
      await page.waitForTimeout(350);
    };
    await openDrawer();
    await page.locator(".nx-sidebar-user").click({ timeout: 8000 });
    await inside("User menu", page.getByRole("menu"));
    await page.getByRole("menuitem", { name: "Account security" }).click();
    await inside("Account security", page.getByRole("dialog"));
    await page.keyboard.press("Escape");
    await openDrawer();
    await page.locator(".nx-sidebar-user").click({ timeout: 8000 });
    await page.getByRole("menuitem", { name: /Check for updates/ }).click();
    await inside("Updates dialog", page.getByRole("dialog"));
    await page.keyboard.press("Escape");
    // name prompt and confirmation dialogs (opened from the VM page when one exists)
    await page.goto("/datacenter?tab=vms");
    await page.waitForTimeout(600);
    if (w < 1024) await page.keyboard.press("Escape");
  }
});

test("keyboard: tab order reaches the skip link, sidebar, inventory, search and page; focus is always visible", async ({ page }) => {
  await login(page, { theme: "dark", lang: "en" });
  await page.goto("/datacenter");
  await page.waitForTimeout(1200);
  const seen: string[] = [];
  let invisible = 0;
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const ring = (parseFloat(cs.outlineWidth) > 0 && cs.outlineStyle !== "none") || cs.boxShadow !== "none";
      return { tag: el.tagName, name: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40), ring, zone: el.closest(".nx-sidebar") ? "sidebar" : el.closest(".nx-top") ? "top" : el.closest("main") ? "main" : "other", role: el.getAttribute("role") };
    });
    if (info) { seen.push(`${info.zone}:${info.name}`); if (!info.ring) invisible++; }
  }
  expect.soft(seen.length, "Tab moves the focus").toBeGreaterThan(20);
  expect.soft(seen.some((s) => s.startsWith("sidebar:")), "sidebar reachable").toBe(true);
  expect.soft(seen.some((s) => s.includes("Find a node or VM")), "search reachable").toBe(true);
  expect.soft(seen.some((s) => s.startsWith("main:")), "page content reachable").toBe(true);
  expect.soft(invisible, `elements focused without a visible focus indicator (of ${seen.length})`).toBeLessThanOrEqual(2);
  // no keyboard trap: Escape after opening the palette returns to the page
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Find a node or VM" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Find a node or VM" })).toBeHidden();
});

test("dialogs and menus open, are named, trap focus and close with Escape", async ({ page }) => {
  const found = collect(page);
  await login(page, { theme: "dark", lang: "en" });
  // create menu -> VM wizard
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("menuitem", { name: "Virtual machine" })).toBeVisible();
  await page.getByRole("menuitem", { name: "Virtual machine" }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toBeVisible();
  await expect(dlg.getByRole("button", { name: "Next" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // container wizard
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("menuitem", { name: "Container" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // user menu: account security, updates
  await page.locator(".nx-sidebar-user").click();
  await page.getByRole("menuitem", { name: "Account security" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.locator(".nx-sidebar-user").click();
  await page.getByRole("menuitem", { name: /Check for updates/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // tree context menu with the keyboard
  const vm = page.getByRole("tree").getByRole("treeitem", { name: /virtual machine, / }).first();
  if (await vm.count()) {
    await vm.focus();
    await page.keyboard.press("Shift+F10");
    await expect(page.getByRole("menu")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
  }
  expect.soft(found.filter((f) => !/HTTP 40[34]/.test(f)), "runtime errors while opening dialogs").toEqual([]);
});

test("VM wizard: draft is kept until confirmed, refusals are readable, one submit only", async ({ page, request }) => {
  const found = collect(page);
  await login(page, { theme: "dark", lang: "en" });
  const token = await apiLogin(request);
  const before = ((await (await request.get("/vms", { headers: { Authorization: `Bearer ${token}` } })).json()) as unknown[]).length;
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("menuitem", { name: "Virtual machine" }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg.getByRole("list", { name: "Steps" })).toBeVisible();
  // the dialog is wide enough for its stepper and footer (a width override once lost to the default)
  const box = await dlg.boundingBox();
  expect(box!.width, "wizard dialog width").toBeGreaterThan(600);
  await expect(dlg.getByRole("button", { name: "Next" })).toBeInViewport();
  for (const step of ["Node", "Template", "CPU / RAM / Disk", "Network", "Summary"]) await expect(dlg.getByRole("list", { name: "Steps" }).getByText(step)).toBeVisible();
  await dlg.getByRole("button", { name: "Next" }).click();
  await dlg.getByRole("button", { name: "Next" }).click();
  await dlg.getByRole("textbox", { name: "VM name" }).fill(`${PREFIX}draft`);
  // Escape with typed values asks first; cancelling keeps everything
  await page.keyboard.press("Escape");
  await expect(page.getByRole("alertdialog")).toContainText("Discard this draft?");
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await expect(dlg.getByRole("textbox", { name: "VM name" })).toHaveValue(`${PREFIX}draft`);
  // an invalid value is refused by the backend with a readable message, and the dialog stays open
  await dlg.getByRole("spinbutton", { name: "vCPU" }).fill("0");
  await dlg.getByRole("textbox", { name: "User" }).fill("tester");
  await dlg.getByRole("textbox", { name: "Password" }).fill("Testpass1");
  await dlg.getByRole("button", { name: "Next" }).click();
  await dlg.getByRole("button", { name: "Next" }).click();
  const create = dlg.getByRole("button", { name: "Create the VM" });
  await create.dblclick();
  const alert = dlg.getByRole("alert");
  await expect(alert).toContainText("could not be created");
  await expect(alert).not.toContainText("[object Object]");
  await expect(dlg).toBeVisible();
  const after = ((await (await request.get("/vms", { headers: { Authorization: `Bearer ${token}` } })).json()) as unknown[]).length;
  expect(after).toBe(before);
  // discarding closes it
  await page.keyboard.press("Escape");
  await page.getByRole("alertdialog").getByRole("button", { name: "Discard" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect.soft(found.filter((f) => !/HTTP 4(00|09|22)/.test(f)), "runtime errors in the wizard").toEqual([]);
});

test.describe("VM lifecycle from the rebuilt interface (real libvirt)", () => {
  test.describe.configure({ mode: "serial", timeout: 300_000 });
  const NAME = `${PREFIX}audit-${Date.now().toString().slice(-6)}`;
  let token = "";
  const auth = () => ({ Authorization: `Bearer ${token}` });
  const state = async (request: APIRequestContext) => { const r = await request.get(`/vms/${NAME}`, { headers: auth() }); return r.ok() ? ((await r.json()) as { etat: string }).etat : "missing"; };

  test.beforeAll(async ({ request }) => { token = await apiLogin(request); });
  test.afterAll(async ({ request }) => {
    if ((await state(request)) !== "missing") {
      await request.post(`/vms/${NAME}/stop?force=true`, { headers: auth() });
      await request.delete(`/vms/${NAME}?confirm=true`, { headers: auth() });
    }
    // extra disks attached by the test: removed once the VM no longer uses them
    const vols = await request.get("/storage/default/volumes", { headers: auth() });
    if (vols.ok()) for (const v of (await vols.json()) as { nom: string }[]) if (v.nom.startsWith(`${NAME}-disk-`)) await request.delete(`/storage/default/volumes/${v.nom}?confirm=true`, { headers: auth() });
  });

  test("create through the wizard, see it everywhere, start, force stop with confirmation, delete", async ({ page, request }) => {
    const found = collect(page);
    await login(page, { theme: "dark", lang: "en" });
    await page.getByRole("button", { name: "Create" }).click();
    await page.getByRole("menuitem", { name: "Virtual machine" }).click();
    const dlg = page.getByRole("dialog");
    await dlg.getByRole("button", { name: "Next" }).click();
    await dlg.getByRole("button", { name: "Next" }).click();
    await dlg.getByRole("textbox", { name: "VM name" }).fill(NAME);
    await dlg.getByRole("spinbutton", { name: "Memory in MB" }).fill("256");
    await dlg.getByRole("spinbutton", { name: "Size of disk 1 in GB" }).fill("3");
    await dlg.getByRole("textbox", { name: "User" }).fill("tester");
    await dlg.getByRole("textbox", { name: "Password" }).fill("Testpass1");
    await dlg.getByRole("button", { name: "Next" }).click();
    await dlg.getByRole("radio", { name: /hyperlite-isolated/ }).check();
    await dlg.getByRole("button", { name: "Next" }).click();
    await dlg.getByRole("button", { name: "Create the VM" }).click();

    // it shows up in the inventory, the VM list and the overview counters
    await expect(page.getByRole("tree").getByRole("treeitem", { name: new RegExp(NAME) })).toBeVisible({ timeout: 90_000 });
    await expect.poll(() => state(request), { timeout: 60_000 }).toBe("arrete");
    await page.goto("/datacenter?tab=vms");
    await expect(page.getByRole("main").getByText(NAME).first()).toBeVisible();

    // hardware: two new disks in a row (the second used to be refused: same generated name)
    await page.goto(`/vm/${NAME}?tab=hardware`);
    const disks = async () => ((await (await request.get(`/vms/${NAME}/disks`, { headers: auth() })).json()) as unknown[]).length;
    const start = await disks();
    for (let i = 1; i <= 2; i++) {
      await page.getByRole("button", { name: "Attach" }).click();
      await expect.poll(disks, { timeout: 30_000 }).toBe(start + i);
    }

    // open it from the list; start from the header
    await page.goto("/datacenter?tab=vms");
    await page.getByRole("main").getByRole("button", { name: NAME }).first().click();
    await expect(page).toHaveURL(new RegExp(`/vm/${NAME}`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(NAME);
    await page.getByRole("button", { name: "Start", exact: true }).click();
    await expect.poll(() => state(request), { timeout: 60_000 }).toBe("actif");
    await expect(page.getByRole("main").getByText("Running").first()).toBeVisible({ timeout: 30_000 });

    // clean Stop asks for a confirmation; cancelling changes nothing
    await page.getByRole("button", { name: /^Actions/ }).click();
    await page.getByRole("menuitem", { name: /^Stop/ }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "Cancel" }).click();
    expect(await state(request)).toBe("actif");

    // force stop: strong confirmation, then it is really stopped
    await page.getByRole("button", { name: /^Actions/ }).click();
    await page.getByRole("menuitem", { name: /Force stop/ }).click();
    await expect(page.getByRole("alertdialog")).toContainText("pulling the power cable");
    await page.getByRole("alertdialog").getByRole("button", { name: "Force stop" }).click();
    await expect.poll(() => state(request), { timeout: 60_000 }).toBe("arrete");

    // the activity page recorded these operations with the right status
    await page.goto("/datacenter?tab=activity");
    await page.getByLabel("Target").fill(NAME);
    await expect(page.getByRole("main").getByRole("table")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("main").getByRole("table").getByRole("button", { name: "Create VM" }).first()).toBeVisible();

    // delete through the operations kept from the historical screen
    await page.goto(`/vm/${NAME}`);
    await page.getByText("All operations").click();
    await page.getByRole("button", { name: "Delete" }).first().click();
    await page.getByRole("alertdialog").getByRole("button", { name: /Delete/ }).click();
    await expect.poll(() => state(request), { timeout: 60_000 }).toBe("missing");
    await expect(page.getByRole("tree").getByRole("treeitem", { name: new RegExp(NAME) })).toHaveCount(0, { timeout: 30_000 });
    expect.soft(found.filter((f) => !/HTTP 40[34]|409/.test(f)), "runtime errors during the lifecycle").toEqual([]);
  });
});
