import { diskController, guestProfile, installationFamily } from "../../utils/osFamily";
import { Card } from "@/components/ui/card";

const FAMILY_LABEL = { kickstart: "Kickstart (unattended)", autoinstall: "Autoinstall (unattended)" };

export default function StepReview({ form, nodes }) {
  const nodeName = nodes.find((n) => n.id === form.node)?.nom || form.node;
  const totalDisk = form.disks.reduce((a, d) => a + d.size_gb, 0);
  const installMode = Boolean(form.iso);
  const importMode = form.importDisk != null;
  const profile = guestProfile(form);
  const osFamily = installationFamily(form);
  const manualInstall = installMode && !osFamily;

  const rows = importMode ? [
    ["Node", nodeName],
    ["Name", form.name || "--"],
    ["System disk", `Imported (${form.importDisk || "--"})`],
    ["Disk controller", diskController(form) === "sata" ? "SATA" : "VirtIO SCSI"],
    ["vCPU", form.vcpu],
    ["Memory", `${form.memory_mb} MB`],
    ["Network", form.network],
    ["Storage pool", form.storagePool || "Default (local)"],
    ["User", "Already present on the imported disk"],
  ] : [
    ["Node", nodeName],
    ["Name", form.name || "--"],
    ["ISO", form.iso || "None"],
    ["Hardware profile", { windows: "Windows", linux: "Linux", other: "Other / generic" }[profile]],
    ["Disk controller", diskController(form) === "sata" ? "SATA" : "VirtIO SCSI"],
    ["Network adapter", profile === "linux" ? "VirtIO" : "Intel E1000e"],
    ["Firmware", "Legacy BIOS (x86-64)"],
    ["Drivers ISO", form.iso ? form.driversIso || "None" : "None"],
    ["System disk", !installMode ? "Debian 12 preinstalled" : `Blank (${osFamily ? FAMILY_LABEL[osFamily] : "manual installation"})`],
    ["vCPU", form.vcpu],
    ["Memory", `${form.memory_mb} MB`],
    ["Disks", `${form.disks.map((d) => `${d.size_gb} GB`).join(" + ")} (${totalDisk} GB total)`],
    ["Network", form.network],
    ["Storage pool", form.storagePool || "Default (local)"],
    manualInstall ? ["User", "Created during the installation"] : ["User", form.username || "--"],
  ];

  return (
    <Card className="p-0 divide-y divide-border">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between px-4 py-2.5 text-sm">
          <span className="text-muted-foreground">{label}</span>
          <span className="text-foreground font-medium">{value}</span>
        </div>
      ))}
    </Card>
  );
}
