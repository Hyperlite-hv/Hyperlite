import { apiLogin, expect, goTo, PREFIX, test, uiLogin } from "../support/fixtures";

// A running task shows its own log and can be cancelled from the task list; the real backend stops it.
test("a running job run shows its log, is cancelled from the task list and ends as cancelled", async ({ page, request }) => {
  const token = await apiLogin(request);
  const auth = { Authorization: `Bearer ${token}` };
  const name = `${PREFIX}slow-${Date.now().toString().slice(-6)}`;
  const created = await request.post("/jobs", { headers: auth, data: { name, steps: [{ cible_type: "host", commande: "sleep 30" }] } });
  expect(created.status()).toBe(201);
  const jobId = (await created.json()).id;
  expect((await request.post(`/jobs/${jobId}/run`, { headers: auth, data: { targets: [], dry_run: false } })).status()).toBe(202);

  await uiLogin(page);
  await goTo(page, "Activity");
  const row = page.getByRole("row").filter({ hasText: name }).first();
  await row.getByRole("button", { name: "Run job" }).click();
  await expect(page.getByText(/Step 1\/1 on the host: sleep 30/)).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Cancel the task" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Cancel this task?");
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel the task" }).click();
  await expect(page.getByText("Cancellation requested by admin")).toBeVisible(); // in the task log
  await expect(page.getByText(/Cancelled by admin/).first()).toBeVisible({ timeout: 15_000 });

  await request.delete(`/jobs/${jobId}`, { headers: auth });
});
