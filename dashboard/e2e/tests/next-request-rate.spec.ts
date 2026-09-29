import { expect, test, uiLogin } from "../support/fixtures";

// A page left open asks the server at its polling pace, not at each render: a load effect depending on a function
// recreated at each render once made the network page call GET /networks hundreds of times a second (each call
// audited). Every page here stays under a few requests per endpoint over a few seconds.
const PAGES = ["/datacenter?tab=reseau", "/datacenter?tab=storage", "/datacenter?tab=backups", "/datacenter?tab=permissions", "/datacenter?tab=vms", "/datacenter?tab=metrics", "/node/local?tab=system"];

test("pages do not flood the server with requests", async ({ page }) => {
  await uiLogin(page);
  for (const url of PAGES) {
    const counts: Record<string, number> = {};
    const count = (r: { resourceType: () => string; url: () => string }) => {
      if (["fetch", "xhr"].includes(r.resourceType())) {
        const k = new URL(r.url()).pathname;
        counts[k] = (counts[k] || 0) + 1;
      }
    };
    page.on("request", count);
    await page.goto(url);
    await page.waitForTimeout(4000);
    page.off("request", count);
    const worst = Object.entries(counts).sort((a, b) => b[1] - a[1])[0] || ["", 0];
    expect(worst[1], `${url}: ${worst[0]} requested ${worst[1]} times in 4 s`).toBeLessThan(12);
  }
});
