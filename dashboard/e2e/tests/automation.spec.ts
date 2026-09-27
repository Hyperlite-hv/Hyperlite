import { existsSync, unlinkSync } from "node:fs";
import { apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";
import type { Page } from "@playwright/test";

const stamp = Date.now().toString().slice(-6);
const OK_JOB = `${PREFIX}job-ok-${stamp}`;
const FAIL_JOB = `${PREFIX}job-fail-${stamp}`;
const MARKER = `/tmp/${PREFIX}marker-${stamp}`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

test.afterAll(async ({ request }) => {
  const jobs = (await (await request.get("/jobs", { headers: auth() })).json()) as Array<{ id: number; name: string }>;
  for (const j of jobs) if (j.name.startsWith(PREFIX)) await request.delete(`/jobs/${j.id}`, { headers: auth() });
  if (existsSync(MARKER)) unlinkSync(MARKER);
});

async function openAutomation(page: Page) {
  await uiLogin(page);
  await page.getByRole("tab", { name: "Automation", exact: true }).click();
}

async function createHostJob(page: Page, name: string, command: string) {
  await page.getByRole("button", { name: "Create a custom job" }).click();
  await page.getByRole("textbox", { name: "Job name" }).fill(name);
  await page.getByRole("textbox", { name: "shell command" }).fill(command);
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("button", { name: `Delete job ${name}` })).toBeVisible();
}

async function runsOf(request: import("@playwright/test").APIRequestContext, name: string) {
  const jobs = (await (await request.get("/jobs", { headers: auth() })).json()) as Array<{ id: number; name: string }>;
  const job = jobs.find((j) => j.name === name)!;
  return (await (await request.get(`/jobs/${job.id}/runs`, { headers: auth() })).json()) as Array<{ statut: string; dry_run: number | boolean }>;
}

test.describe("Automation jobs (real backend)", () => {
  test("a dry run does not execute anything, a real run does, and the history shows both", async ({ page, request }) => {
    await openAutomation(page);
    await createHostJob(page, OK_JOB, `touch ${MARKER}`);
    await page.getByRole("button", { name: `Dry run ${OK_JOB}` }).click();
    await expect.poll(async () => (await runsOf(request, OK_JOB)).length, { timeout: 30_000 }).toBe(1);
    expect(existsSync(MARKER), "a dry run must not run the command").toBe(false);

    await page.getByRole("button", { name: `Run ${OK_JOB}`, exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Run", exact: true }).click();
    await expect.poll(async () => (await runsOf(request, OK_JOB)).filter((r) => !r.dry_run).map((r) => r.statut), { timeout: 30_000 }).toEqual(["succes"]);
    expect(existsSync(MARKER), "the real run executed the command").toBe(true);
  });

  test("a failing command is recorded as a failed run", async ({ page, request }) => {
    await openAutomation(page);
    await createHostJob(page, FAIL_JOB, "exit 3");
    await page.getByRole("button", { name: `Run ${FAIL_JOB}`, exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Run", exact: true }).click();
    await expect.poll(async () => (await runsOf(request, FAIL_JOB)).map((r) => r.statut), { timeout: 30_000 }).toEqual(["echec"]);
  });

  test("a job name that already exists is refused", async ({ page }) => {
    await openAutomation(page);
    await page.getByRole("button", { name: "Create a custom job" }).click();
    await page.getByRole("textbox", { name: "Job name" }).fill(OK_JOB);
    await page.getByRole("textbox", { name: "shell command" }).fill("true");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByRole("alert").or(page.getByText(/already exists|already used/i)).first()).toBeVisible();
  });

  test("deleting a job asks for a confirmation naming it", async ({ page, request }) => {
    await openAutomation(page);
    await page.getByRole("button", { name: `Delete job ${FAIL_JOB}` }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText(FAIL_JOB);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("button", { name: `Delete job ${FAIL_JOB}` })).toBeVisible();
    await page.getByRole("button", { name: `Delete job ${FAIL_JOB}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect(page.getByRole("button", { name: `Delete job ${FAIL_JOB}` })).toHaveCount(0);
    const jobs = (await (await request.get("/jobs", { headers: auth() })).json()) as Array<{ name: string }>;
    expect(jobs.some((j) => j.name === FAIL_JOB)).toBe(false);
  });

  test("a read-only user cannot create or run jobs (backend refuses)", async ({ request }) => {
    const name = `${PREFIX}ro-${stamp}`;
    const made = await request.post("/auth/users", { headers: auth(), data: { username: name, password: "Readonly-Pass1", role: "observateur" } });
    expect(made.ok(), await made.text()).toBe(true);
    const login = await request.post("/auth/login", { form: { username: name, password: "Readonly-Pass1" } });
    const roToken = ((await login.json()) as { access_token: string }).access_token;
    const denied = await request.post("/jobs", { headers: { Authorization: `Bearer ${roToken}` }, data: { name: `${PREFIX}denied`, steps: [] } });
    expect(denied.status()).toBe(403);
    await request.delete(`/auth/users/${name}`, { headers: auth() });
  });
});
