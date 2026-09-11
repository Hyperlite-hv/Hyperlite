// Mock uniquement : pas de route backend exposant ces informations aujourd'hui.
export default function NodeSystemTab({ resource: node }) {
  if (!node) return null;
  const rows = [
    ["Noyau", "Linux 6.1.0-53-amd64"],
    ["Hyperviseur", "QEMU/KVM via libvirt"],
    ["Service Hyperlite", "hyperlite.service (systemd, actif)"],
    ["Python", "3.11.2 / FastAPI 0.141.1 / uvicorn 0.52.4"],
    ["Coeurs CPU", `${node.cpu_coeurs}`],
    ["Memoire totale", `${(node.memoire_totale_mo / 1024).toFixed(0)} Go`],
  ];
  return (
    <div className="card divide-y divide-anthracite-600">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between px-4 py-3 text-sm">
          <span className="text-anthracite-300">{label}</span>
          <span className="text-anthracite-100 font-mono">{value}</span>
        </div>
      ))}
    </div>
  );
}
