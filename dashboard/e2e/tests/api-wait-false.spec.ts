import { apiLogin, expect, PREFIX, test } from "../support/fixtures";
import type { APIRequestContext } from "@playwright/test";

// `?wait=false` on the long operations, against a real libvirt: each answers 202 with its task at once, and the task
// ends with the object made (what the Terraform provider relies on, docs/design/terraform-provider.md section 6).
const stamp = Date.now().toString().slice(-6);
const SRC = `${PREFIX}wf-src-${stamp}`;
const CLONE = `${PREFIX}wf-clone-${stamp}`;
const TEMPLATE = `${PREFIX}wf-tpl-${stamp}`;
const DEPLOYED = `${PREFIX}wf-dep-${stamp}`;
const VMS = [SRC, CLONE, DEPLOYED];
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.describe.configure({ mode: "serial", timeout: 300_000 });

async function started(request: APIRequestContext, path: string, data: object, name: string) {
  const res = await request.post(path, { headers: auth(), data });
  expect(res.status(), await res.text()).toBe(202);
  const body = (await res.json()) as { task_id: string; status: string; name: string };
  expect(body.status).toBe("running");
  expect(body.name).toBe(name);
  return body.task_id;
}

async function finished(request: APIRequestContext, taskId: string) {
  await expect
    .poll(async () => ((await (await request.get(`/tasks/${taskId}`, { headers: auth() })).json()) as { status: string }).status, { timeout: 240_000, intervals: [1_000] })
    .not.toMatch(/^(pending|running)$/);
  const task = (await (await request.get(`/tasks/${taskId}`, { headers: auth() })).json()) as { status: string; error: string | null };
  expect(task.error).toBeNull();
  expect(task.status).toBe("succeeded");
}

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

test.afterAll(async ({ request }) => {
  for (const name of VMS) {
    if ((await request.get(`/vms/${name}`, { headers: auth() })).ok()) {
      await request.post(`/vms/${name}/stop?force=true`, { headers: auth() });
      await request.delete(`/vms/${name}?confirm=true`, { headers: auth() });
    }
  }
  await request.delete(`/templates/${TEMPLATE}?confirm=true`, { headers: auth() });
});

test("a VM is created, cloned, copied to a template and deployed, each answering its task at once", async ({ request }) => {
  const create = { name: SRC, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1" };
  await finished(request, await started(request, "/vms?wait=false", create, SRC));
  expect((await request.get(`/vms/${SRC}`, { headers: auth() })).ok()).toBe(true);

  // A second creation under the same name is refused by the checks, before any task.
  const again = await request.post("/vms?wait=false", { headers: auth(), data: create });
  expect(again.status()).toBe(422);

  await finished(request, await started(request, `/vms/${SRC}/clone?wait=false`, { new_name: CLONE }, CLONE));
  expect((await request.get(`/vms/${CLONE}`, { headers: auth() })).ok()).toBe(true);

  await finished(request, await started(request, `/templates/from-vm/${CLONE}?wait=false`, { template_name: TEMPLATE, keep_vm: true }, TEMPLATE));
  // The fixture reads a list ({items, total}) as its items, as the dashboard does.
  const templates = (await (await request.get("/templates", { headers: auth() })).json()) as Array<{ name: string }>;
  expect(templates.map((t) => t.name)).toContain(TEMPLATE);

  await finished(request, await started(request, `/templates/${TEMPLATE}/deploy?wait=false`, { new_name: DEPLOYED }, DEPLOYED));
  const deployed = (await (await request.get(`/vms/${DEPLOYED}`, { headers: auth() })).json()) as { state: string };
  expect(deployed.state).toBe("stopped");
});
