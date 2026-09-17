// Tuile de statistique (refonte 2026-09-17, en-tete du tableau de bord
// Datacenter) -- badge rond teinte + gros chiffre, reutilisable partout ou
// ce motif a du sens (uniquement DatacenterSummaryTab pour l'instant).
const TONES = {
  blue: "bg-accent-blue/10 text-accent-blue",
  green: "bg-status-running/10 text-status-running",
  gray: "bg-anthracite-600 text-anthracite-300",
  amber: "bg-status-warning/10 text-status-warning",
};

export default function StatTile({ icon: Icon, label, value, foot, tone = "blue" }) {
  return (
    <div className="card flex items-center gap-3.5 p-4">
      <div className={`flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-full ${TONES[tone] || TONES.blue}`}>
        <Icon size={22} />
      </div>
      <div className="min-w-0">
        <div className="truncate text-[12px] font-semibold text-anthracite-300">{label}</div>
        <div className="font-mono text-[24px] font-extrabold leading-tight text-anthracite-100">{value}</div>
        {foot && <div className="truncate text-[11px] text-anthracite-400">{foot}</div>}
      </div>
    </div>
  );
}
