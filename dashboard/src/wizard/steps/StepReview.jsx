export default function StepReview({ form, nodes }) {
  const nodeName = nodes.find((n) => n.id === form.node)?.nom || form.node;
  const totalDisk = form.disks.reduce((a, d) => a + d.size_gb, 0);

  const rows = [
    ["Noeud", nodeName],
    ["Nom", form.name || "--"],
    ["ISO", form.iso || "Aucune"],
    ["vCPU", form.vcpu],
    ["Memoire", `${form.memory_mb} Mo`],
    ["Disques", `${form.disks.map((d) => `${d.size_gb} Go`).join(" + ")} (${totalDisk} Go total)`],
    ["Reseau", form.network],
    ["Utilisateur", form.username || "--"],
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
