import { detectOsFamily } from "../../utils/osFamily";

const FAMILY_LABEL = { kickstart: "Kickstart (automatisé)", autoinstall: "Autoinstall (automatisé)" };

export default function StepReview({ form, nodes }) {
  const nodeName = nodes.find((n) => n.id === form.node)?.nom || form.node;
  const totalDisk = form.disks.reduce((a, d) => a + d.size_gb, 0);
  const installMode = Boolean(form.iso);
  const importMode = form.importDisk != null;
  const osFamily = detectOsFamily(form.iso);
  const manualInstall = installMode && !osFamily;

  const rows = importMode ? [
    ["Nœud", nodeName],
    ["Nom", form.name || "--"],
    ["Disque système", `Importé (${form.importDisk || "--"})`],
    ["vCPU", form.vcpu],
    ["Mémoire", `${form.memory_mb} Mo`],
    ["Réseau", form.network],
    ["Pool de stockage", form.storagePool || "Par défaut (local)"],
    ["Utilisateur", "Déjà présent sur le disque importé"],
  ] : [
    ["Nœud", nodeName],
    ["Nom", form.name || "--"],
    ["ISO", form.iso || "Aucune"],
    ["Disque système", !installMode ? "Debian 12 préinstallé" : `Vierge (${osFamily ? FAMILY_LABEL[osFamily] : "installation manuelle"})`],
    ["vCPU", form.vcpu],
    ["Mémoire", `${form.memory_mb} Mo`],
    ["Disques", `${form.disks.map((d) => `${d.size_gb} Go`).join(" + ")} (${totalDisk} Go total)`],
    ["Réseau", form.network],
    ["Pool de stockage", form.storagePool || "Par défaut (local)"],
    manualInstall ? ["Utilisateur", "Créé pendant l'installation"] : ["Utilisateur", form.username || "--"],
  ];

  return (
    <div className="card divide-y divide-anthracite-600">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between px-4 py-2.5 text-sm">
          <span className="text-anthracite-400">{label}</span>
          <span className="text-anthracite-100 font-medium">{value}</span>
        </div>
      ))}
    </div>
  );
}
