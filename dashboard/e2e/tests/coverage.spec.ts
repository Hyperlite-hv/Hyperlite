import { createServer, type Server } from "node:net";
import type { AddressInfo } from "node:net";
import { apiLogin, expect, PREFIX, test, uiLogin, ADMIN } from "../support/fixtures";

const stamp = Date.now().toString().slice(-6);
const VM = `${PREFIX}snap-${stamp}`;
const USER = `${PREFIX}sess-${stamp}`;
const USER_PASSWORD = "Session-Pass1";
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.describe.configure({ mode: "serial", timeout: 180_000 });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

test.describe("Email (SMTP) notifications", () => {
  test("a test message is delivered to a real SMTP receiver and the password is never returned", async ({ request }) => {
    const session: string[] = [];
    const server: Server = createServer((socket) => {
      let inData = false;
      let buffer = "";
      socket.write("220 e2e ESMTP\r\n");
      socket.on("data", (chunk) => {
        buffer += chunk.toString();
        let idx: number;
        while ((idx = buffer.indexOf("\r\n")) >= 0) {
          const line = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          session.push(line);
          if (inData) {
            if (line === ".") {
              inData = false;
              socket.write("250 queued\r\n");
            }
          } else if (/^EHLO|^HELO/i.test(line)) socket.write("250 e2e\r\n");
          else if (/^DATA/i.test(line)) {
            inData = true;
            socket.write("354 go\r\n");
          } else if (/^QUIT/i.test(line)) socket.end("221 bye\r\n");
          else socket.write("250 ok\r\n");
        }
      });
    });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const port = (server.address() as AddressInfo).port;
    const name = `${PREFIX}smtp-${stamp}`;
    try {
      const created = await request.post("/notifications/channels", {
        headers: auth(),
        data: { type: "email", name, events: [], config: { smtp_host: "127.0.0.1", smtp_port: String(port), smtp_password: "super-secret-value", from_addr: "hyperlite@example.test", to_addr: "ops@example.test", use_tls: false } },
      });
      expect(created.ok(), await created.text()).toBe(true);
      const id = ((await created.json()) as { id: number }).id;
      const sent = await request.post(`/notifications/channels/${id}/test`, { headers: auth() });
      expect(sent.ok(), await sent.text()).toBe(true);
      await expect.poll(() => session.some((l) => /^Subject: \[Hyperlite\]/i.test(l)), { timeout: 10_000 }).toBe(true);
      expect(session.some((l) => /RCPT TO:<ops@example.test>/i.test(l))).toBe(true);
      const listed = await (await request.get("/notifications/channels", { headers: auth() })).text();
      expect(listed).not.toContain("super-secret-value");
      await request.delete(`/notifications/channels/${id}`, { headers: auth() });
    } finally {
      server.close();
    }
  });

  test("an email channel with an unreachable server reports a readable error", async ({ request }) => {
    const name = `${PREFIX}smtp-bad-${stamp}`;
    const created = await request.post("/notifications/channels", {
      headers: auth(),
      data: { type: "email", name, events: [], config: { smtp_host: "127.0.0.1", smtp_port: "1", from_addr: "a@example.test", to_addr: "b@example.test", use_tls: false } },
    });
    const id = ((await created.json()) as { id: number }).id;
    const sent = await request.post(`/notifications/channels/${id}/test`, { headers: auth() });
    expect(sent.ok()).toBe(false);
    expect(await sent.text()).not.toMatch(/Traceback|Champs|Erreur/);
    await request.delete(`/notifications/channels/${id}`, { headers: auth() });
  });
});

test.describe("Snapshot restore (real backend)", () => {
  test.beforeAll(async ({ request }) => {
    const res = await request.post("/vms", { headers: auth(), data: { name: VM, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1" } });
    expect(res.status()).toBe(201);
  });
  test.afterAll(async ({ request }) => {
    await request.post(`/vms/${VM}/stop?force=true`, { headers: auth() });
    await request.delete(`/vms/${VM}?confirm=true`, { headers: auth() });
  });

  test("restoring a snapshot brings the VM configuration back after a confirmation naming it", async ({ page, request }) => {
    const snap = `${PREFIX}s1-${stamp}`;
    const made = await request.post(`/vms/${VM}/snapshots`, { headers: auth(), data: { name: snap } });
    expect(made.ok(), await made.text()).toBe(true);
    await expect.poll(async () => ((await (await request.get(`/vms/${VM}/snapshots`, { headers: auth() })).json()) as Array<{ nom: string }>).map((s) => s.nom), { timeout: 60_000 }).toContain(snap);

    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(VM) }).click();
    await page.getByRole("tab", { name: "Options", exact: true }).click();
    await page.getByRole("spinbutton", { name: "Memory in MB" }).fill("512");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(async () => ((await (await request.get(`/vms/${VM}`, { headers: auth() })).json()) as { memoire_mo: number }).memoire_mo).toBe(512);

    await page.getByRole("tab", { name: "Snapshots", exact: true }).click();
    await page.getByRole("button", { name: `Restore snapshot ${snap}` }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText(snap);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    expect(((await (await request.get(`/vms/${VM}`, { headers: auth() })).json()) as { memoire_mo: number }).memoire_mo, "cancel changes nothing").toBe(512);

    await page.getByRole("button", { name: `Restore snapshot ${snap}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Restore" }).click();
    await expect.poll(async () => ((await (await request.get(`/vms/${VM}`, { headers: auth() })).json()) as { memoire_mo: number }).memoire_mo, { timeout: 60_000 }).toBe(256);
  });
});

test.describe("Two users at the same time", () => {
  test("a user removed by an administrator is signed out on the next action, while the admin session keeps working", async ({ browser, request }) => {
    const made = await request.post("/auth/users", { headers: auth(), data: { username: USER, password: USER_PASSWORD, role: "observateur" } });
    expect(made.ok(), await made.text()).toBe(true);
    const adminCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    const userCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    const baseURL = test.info().project.use.baseURL as string;
    const adminPage = await adminCtx.newPage();
    const userPage = await userCtx.newPage();
    try {
      await adminPage.goto(baseURL);
      await uiLogin(adminPage, ADMIN.username, ADMIN.password);
      await userPage.goto(baseURL);
      await uiLogin(userPage, USER, USER_PASSWORD);

      const removed = await request.delete(`/auth/users/${USER}`, { headers: auth() });
      expect(removed.ok(), await removed.text()).toBe(true);

      await userPage.reload();
      await expect(userPage.getByRole("button", { name: "Sign in" })).toBeVisible();
      await adminPage.reload();
      await expect(adminPage.getByText("Datacenter").first()).toBeVisible();
    } finally {
      await adminCtx.close();
      await userCtx.close();
      await request.delete(`/auth/users/${USER}`, { headers: auth() });
    }
  });

  test("a change made by one administrator session is visible to another session after a reload", async ({ browser, request }) => {
    const group = `${PREFIX}grp-${stamp}`;
    const baseURL = test.info().project.use.baseURL as string;
    const a = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
    const b = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
    try {
      await a.goto(baseURL);
      await uiLogin(a);
      await b.goto(baseURL);
      await uiLogin(b);
      const made = await request.post("/groups", { headers: auth(), data: { name: group } });
      expect(made.ok(), await made.text()).toBe(true);
      await b.getByRole("tab", { name: "Permissions", exact: true }).click();
      await b.reload();
      await b.getByRole("tab", { name: "Permissions", exact: true }).click();
      await expect(b.getByText(group).first()).toBeVisible();
    } finally {
      await a.context().close();
      await b.context().close();
      await request.delete(`/groups/${group}`, { headers: auth() });
    }
  });
});
