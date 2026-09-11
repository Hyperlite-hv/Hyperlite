import { Cpu, MemoryStick, HardDrive, Network } from "lucide-react";

// Correspond a GET /vms/{name} + GET /vms/{name}/disks + GET /vms/{name}/network
// cote backend reel (deja fonctionnels) : memoire les fusionner ici plutot que de
// tout re-derivé du seul objet `vm` mock une fois l'API branchee.
export default function VMHardwareTab({ resource: vm }) {
  if (!vm) return null;
  const rows = [
    { icon: Cpu, label: "Processeur", value: `${vm.vcpu} vCPU` },
    { icon: MemoryStick, label: "Memoire", value: `${vm.memoire_mo} Mo` },
    { icon: HardDrive, label: "Disque principal (sda, virtio-scsi)", value: `${vm.disque_go} Go` },
    { icon: Network, label: "Interface reseau (net0, virtio)", value: "default (NAT)" },
  ];
  return (
    <div className="card divide-y divide-anthracite-600">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-3 px-4 py-3">
          <r.icon size={16} className="text-anthracite-400 shrink-0" />
          <span className="text-sm text-anthracite-200 flex-1">{r.label}</span>
          <span className="text-sm text-anthracite-100 font-mono">{r.value}</span>
        </div>
      ))}
    </div>
  );
}
