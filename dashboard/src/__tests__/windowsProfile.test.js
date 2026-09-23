import { describe, expect, it } from "vitest";
import { detectWindows, diskController, guestProfile, installationFamily, isWindowsInstall } from "../utils/osFamily";

describe("Windows installation profile", () => {
  it.each(["fr-fr_windows_server_2025_x64.iso", "Win11_English.iso", "SERVER-2022.iso", "win10.iso"])("detects %s", (name) => {
    expect(detectWindows(name)).toBe(true);
  });
  it.each([null, "ubuntu-26.04-live-server.iso", "virtio-win.iso", "darwin.iso"])("does not misclassify %s", (name) => {
    expect(detectWindows(name)).toBe(false);
  });
  it("supports renamed media and explicit overrides", () => {
    expect(isWindowsInstall({ iso: "installer.iso", guestOs: "windows" })).toBe(true);
    expect(isWindowsInstall({ iso: "windows.iso", guestOs: "linux" })).toBe(false);
    expect(isWindowsInstall({ iso: "", guestOs: "windows" })).toBe(false);
  });
  it("chooses controllers independently from guest naming", () => {
    expect(diskController({ iso: "windows.iso" })).toBe("sata");
    expect(diskController({ iso: "ubuntu.iso" })).toBe("virtio-scsi");
    expect(diskController({ iso: "windows.iso", diskController: "virtio-scsi" })).toBe("virtio-scsi");
    expect(diskController({ iso: "ubuntu.iso", diskController: "sata" })).toBe("sata");
    expect(diskController({ importDisk: "windows.qcow2" })).toBe("virtio-scsi");
  });
  it("gives unknown installers generic hardware without unattended Linux setup", () => {
    expect(guestProfile({ iso: "freebsd.iso" })).toBe("other");
    expect(diskController({ iso: "installer.iso" })).toBe("sata");
    expect(guestProfile({ iso: "debian-13.iso" })).toBe("linux");
    expect(installationFamily({ iso: "ubuntu.iso", guestOs: "other" })).toBe(null);
    expect(installationFamily({ iso: "ubuntu.iso", guestOs: "windows" })).toBe(null);
    expect(installationFamily({ iso: "ubuntu.iso" })).toBe("autoinstall");
  });
});
