import { expect, test, uiLogin } from "../support/fixtures";
import type { Page, Route } from "@playwright/test";

// The terminals size themselves to what is drawn: the rows and columns sent to the shell all fit in the visible box,
// on a laptop at 125 % display scaling and after the window changes size (the fit used to count the box's padding
// and border: the last row and column were cut). The shell is served by the test: only the sizing is checked here.
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

async function fakeShell(page: Page, sizes: { cols: number; rows: number }[]) {
  await page.route(/\/host\/terminal-ticket$/, (r) => (api(r) ? r.fulfill(json({ ticket: "e2e" })) : r.fallback()));
  await page.routeWebSocket(/\/host\/terminal/, (ws) => {
    ws.onMessage((m) => { if (typeof m === "string" && m.startsWith("\x00")) sizes.push(JSON.parse(m.slice(1))); });
    let out = "";
    for (let i = 1; i <= 80; i++) out += `line ${i} ${"x".repeat(300)}\r\n`;
    setTimeout(() => ws.send(out + "root@e2e:~# "), 200);
  });
}

// Bottom of the last row and right edge of the screen against the inside of the terminal box (padding excluded).
async function overflow(page: Page) {
  return page.locator(".nx-term").first().evaluate((box) => {
    const cs = getComputedStyle(box), r = box.getBoundingClientRect();
    const bottom = r.bottom - parseFloat(cs.borderBottomWidth) - parseFloat(cs.paddingBottom);
    const right = r.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
    const last = box.querySelector(".xterm-rows")!.lastElementChild!.getBoundingClientRect();
    const screen = box.querySelector(".xterm-screen")!.getBoundingClientRect();
    return { rows: Math.round(last.bottom - bottom), cols: Math.round(screen.right - right) };
  });
}

test.use({ viewport: { width: 1093, height: 560 }, deviceScaleFactor: 1.25 });

test("node shell: every row and column sent to the shell is visible, before and after a resize", async ({ page }) => {
  const sizes: { cols: number; rows: number }[] = [];
  await uiLogin(page);
  await fakeShell(page, sizes);
  await page.goto("/node/local?tab=shell");
  await page.getByRole("main").getByRole("button", { name: "Open the shell" }).click();
  await expect.poll(() => sizes.length).toBeGreaterThan(0);
  await expect(page.locator(".xterm-rows")).toContainText("root@e2e:~#");
  let o = await overflow(page);
  expect(o.rows, "last row inside the box").toBeLessThanOrEqual(0);
  expect(o.cols, "last column inside the box").toBeLessThanOrEqual(0);

  const before = sizes[sizes.length - 1];
  await page.setViewportSize({ width: 1400, height: 800 });
  await expect.poll(() => sizes[sizes.length - 1].cols).toBeGreaterThan(before.cols); // the new size reached the shell
  o = await overflow(page);
  expect(o.rows).toBeLessThanOrEqual(0);
  expect(o.cols).toBeLessThanOrEqual(0);
});

test("host shell window: the terminal shrinks with a small window instead of running off its bottom", async ({ page }) => {
  const sizes: { cols: number; rows: number }[] = [];
  await uiLogin(page);
  await fakeShell(page, sizes);
  await page.goto("/host-shell");
  await expect(page.locator(".xterm-rows")).toContainText("root@e2e:~#");
  await page.setViewportSize({ width: 700, height: 420 });
  await expect.poll(async () => (await overflow(page)).rows).toBeLessThanOrEqual(0);
  const box = await page.locator(".nx-term").first().boundingBox();
  expect(box!.y + box!.height).toBeLessThanOrEqual(420);
});
