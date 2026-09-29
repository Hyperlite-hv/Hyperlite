import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Browsing a backup's files from the VM's Backups tab: filesystem, folders, a file downloaded, the session closed
// with the drawer. The endpoints are served by the test (the reader: tests/test_file_restore.py, libguestfs checked
// by hand on a real image).
const NAME = "e2e-fr-web";
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = { nom: NAME, etat: "arrete", id: null, uuid: NAME, vcpu: 1, memoire_mo: 1024, ip: null, utilisateur_ssh: null, uptime_s: null, os: "Debian", stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios" };

test("file-level restore: browse a backup and download a file", async ({ page }) => {
  const calls: string[] = [];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/backups$`), (r) => (api(r) ? r.fulfill(json([{ id: 41, vm_name: NAME, statut: "termine", mode: "froid", cree_le: "2026-09-01T02:00:00Z", taille_octets: 1048576, chemin: "/b/41", verification: null }])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/backup-schedule$`), (r) => (api(r) ? r.fulfill(json(null)) : r.fallback()));
  await page.route(/\/backups\/41\/files$/, (r) => { calls.push("open"); return r.fulfill(json({ session: "s1", vm: NAME, filesystems: [{ device: "/dev/sda1", type: "ext4", size: 10485760, label: "root" }] })); });
  await page.route(/\/file-restore\/s1\/ls\?.*/, (r) => {
    const path = new URL(r.request().url()).searchParams.get("path");
    calls.push(`ls ${path}`);
    return r.fulfill(json({ entries: path === "/" ? [{ name: "etc", type: "dir", size: 4096, mtime: 1790000000 }, { name: "notes.txt", type: "file", size: 18, mtime: 1790000000 }] : [{ name: "app.conf", type: "file", size: 6, mtime: 1790000000 }] }));
  });
  await page.route(/\/file-restore\/s1\/download\?.*/, (r) => {
    calls.push(`get ${new URL(r.request().url()).searchParams.get("path")}`);
    return r.fulfill({ status: 200, headers: { "content-disposition": 'attachment; filename="app.conf"' }, body: "cfg=1\n" });
  });
  await page.route(/\/file-restore\/s1$/, (r) => { calls.push("close"); return r.fulfill(json({ message: "Closed" })); });

  await page.goto(`/vm/${NAME}?tab=backup`);
  await page.getByRole("button", { name: "Browse the files of backup 41" }).click();
  const drawer = page.getByRole("dialog", { name: /Files of e2e-fr-web/ });
  const list = drawer.getByRole("list", { name: "Content of the folder" });
  await expect(list.getByRole("button", { name: "notes.txt", exact: true })).toHaveCount(0); // a file is not a link
  await list.getByRole("button", { name: "etc", exact: true }).click();
  await expect(drawer.getByRole("navigation", { name: "Folder" })).toContainText("etc");
  const download = page.waitForEvent("download");
  await list.getByRole("button", { name: "Download app.conf" }).click();
  expect((await download).suggestedFilename()).toBe("app.conf");
  await drawer.getByRole("button", { name: "Close" }).click();
  await expect.poll(() => calls).toEqual(["open", "ls /", "ls /etc", "get /etc/app.conf", "close"]);
});
