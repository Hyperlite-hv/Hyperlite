import { expect, test, uiLogin } from "../support/fixtures";

// The Metrics page against the real backend: the Prometheus endpoint, and a Graphite server added, tested (nothing
// listens on its port, so the failure and its cause are shown), paused and deleted.
const NAME = `e2e-graphite-${Date.now().toString().slice(-6)}`;

test("metrics: Prometheus endpoint and a push server", async ({ page }) => {
  await uiLogin(page);
  await page.goto("/datacenter?tab=metrics");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1, name: "Metrics" })).toBeVisible();
  await expect(main.getByText(/\/metrics$/)).toBeVisible();
  await expect(main.getByLabel("Scrape job for prometheus.yml")).toContainText("job_name: hyperlite");

  await main.getByRole("button", { name: "Add a metric server" }).click();
  const drawer = page.getByRole("dialog", { name: "Add a metric server" });
  await drawer.getByLabel("Name").fill(NAME);
  await drawer.getByLabel("Type").selectOption("graphite");
  await drawer.getByLabel("Host").fill("127.0.0.1");
  await drawer.getByLabel("Port").fill("9");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Metric server saved")).toBeVisible();

  const row = main.getByRole("row", { name: new RegExp(NAME) });
  await expect(row).toContainText("127.0.0.1:9");
  await row.getByRole("button", { name: `Test ${NAME}` }).click();
  await expect(page.getByText("Sending failed", { exact: true })).toBeVisible();
  await expect(row).toContainText("Error:");

  await row.getByRole("button", { name: `Edit ${NAME}` }).click();
  await page.getByRole("dialog", { name: "Edit the metric server" }).getByLabel("Send the samples to this server").uncheck();
  await page.getByRole("dialog", { name: "Edit the metric server" }).getByRole("button", { name: "Save" }).click();
  await expect(row).toContainText("paused");

  await row.getByRole("button", { name: `Delete ${NAME}` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
  await expect(main.getByRole("row", { name: new RegExp(NAME) })).toHaveCount(0);
});
