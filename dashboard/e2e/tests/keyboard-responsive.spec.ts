import type { Page } from "@playwright/test";
import { expect, test, uiLogin } from "../support/fixtures";

// The top bar "Create" menu, then its "Virtual machine" entry: the VM wizard.
async function openVmWizard(page: Page) {
  await page.getByRole("banner").getByRole("button", { name: "Create" }).click();
  await page.getByRole("menuitem", { name: "Virtual machine" }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toBeVisible();
  return dlg;
}

test.describe("Keyboard-only use", () => {
  test("the main navigation is reachable with Tab and shows a visible focus indicator", async ({ page }) => {
    await uiLogin(page);
    await page.locator("body").click({ position: { x: 1, y: 1 } });
    const reached = new Set<string>();
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Tab");
      const info = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const cs = getComputedStyle(el);
        const visible = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none";
        return { name: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40), visible };
      });
      if (info) {
        reached.add(info.name);
        expect(info.visible, `focus indicator visible on "${info.name}"`).toBe(true);
      }
    }
    expect([...reached].some((n) => /^Create$/.test(n))).toBe(true);
    expect([...reached].some((n) => /Storage/.test(n))).toBe(true);
  });

  test("the VM wizard opens with the keyboard, keeps the focus inside, and Escape closes it and restores the focus", async ({ page }) => {
    await uiLogin(page);
    const opener = page.getByRole("banner").getByRole("button", { name: "Create" });
    await opener.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "Virtual machine" })).toBeFocused();
    await page.keyboard.press("Enter");
    const dlg = page.getByRole("dialog");
    await expect(dlg).toBeVisible();
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press("Tab");
      expect(await dlg.evaluate((d) => d.contains(document.activeElement)), "focus stays in the dialog").toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(dlg).toHaveCount(0);
    await expect(opener).toBeFocused();
  });

  test("the account security dialog closes with Escape", async ({ page }) => {
    await uiLogin(page);
    await page.locator(".nx-sidebar-user").click();
    await page.getByRole("menuitem", { name: "Account security" }).click();
    await expect(page.getByRole("dialog", { name: "Account security" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Account security" })).toHaveCount(0);
  });

  test("the search palette opens a node with the keyboard", async ({ page, request }) => {
    const { hostname } = await (await request.get("/health")).json();
    await uiLogin(page);
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Find a node or VM" });
    await palette.getByRole("combobox").fill(hostname.split(".")[0]);
    await expect(palette.getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/node\//);
  });

  test("tabs can be changed with the keyboard", async ({ page }) => {
    await uiLogin(page);
    const perf = page.getByRole("main").getByRole("tab", { name: "Performance", exact: true });
    await perf.focus();
    await page.keyboard.press("Enter");
    await expect(perf).toHaveAttribute("aria-selected", "true");
  });
});

const viewports = [
  { name: "large desktop", width: 1920, height: 1080 },
  { name: "laptop", width: 1366, height: 768 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "phone", width: 390, height: 844 },
  { name: "200% zoom equivalent", width: 640, height: 480 },
];

test.describe("Responsive layout", () => {
  for (const vp of viewports) {
    test(`${vp.name} (${vp.width}px): no horizontal page scroll and critical actions reachable`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await uiLogin(page);
      for (const tab of ["summary", "reseau", "storage", "permissions"]) {
        await page.goto(tab === "summary" ? "/datacenter" : `/datacenter?tab=${tab}`);
        await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toBeVisible();
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, `page overflows horizontally by ${overflow}px on ${tab}`).toBeLessThanOrEqual(1);
      }
      // Creating a VM must stay possible at every size.
      const dlg = await openVmWizard(page);
      const box = await dlg.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(vp.width + 1);
    });
  }

  test("on a phone the navigation is reachable through the menu button", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await uiLogin(page);
    await page.getByRole("button", { name: "Toggle sidebar" }).click();
    await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Storage", exact: true })).toBeInViewport();
  });
});
