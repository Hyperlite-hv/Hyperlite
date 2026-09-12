// Jauge circulaire (CPU / RAM / stockage). Pur SVG, pas de dependance externe.
// Vire au orange puis au rouge au-dela d'un seuil critique, quelle que soit la
// couleur "de base" de la metrique -- c'est ce qui permet de reperer un
// probleme en un coup d'oeil (reflexe Proxmox/vSphere), pas juste decoratif.
//
// Quand ratio est null/undefined (donnee pas encore exposee par le backend),
// affiche un anneau pointille "n/a" plutot qu'un texte brut a cote -- garde le
// meme encombrement visuel qu'une jauge avec donnee, pour ne pas desequilibrer
// une rangee de plusieurs jauges (ex. Resume Datacenter quand CPU/RAM totale
// ne sont pas encore disponibles).
function effectiveColor(ratio, base) {
  if (ratio >= 0.9) return "text-status-error";
  if (ratio >= 0.75) return "text-accent-orange";
  return base;
}

export default function GaugeRing({ label, ratio, valueLabel, size = 96, colorClass = "text-accent-blue" }) {
  const hasData = ratio != null;
  const clamped = Math.max(0, Math.min(1, ratio ?? 0));
  const stroke = 8;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - clamped);
  const pct = Math.round(clamped * 100);
  const color = effectiveColor(clamped, colorClass);

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} className="stroke-anthracite-600" fill="none" />
          {hasData ? (
            <circle
              cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} fill="none"
              strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
              className={`${color} transition-[stroke-dashoffset,color] duration-700 ease-out`}
              stroke="currentColor"
            />
          ) : (
            <circle
              cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} fill="none"
              strokeDasharray="3 6" strokeLinecap="round"
              className="text-anthracite-500" stroke="currentColor"
            />
          )}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={hasData ? "text-lg font-semibold text-anthracite-100" : "text-xs font-medium text-anthracite-500"}>
            {hasData ? `${pct}%` : "n/a"}
          </span>
        </div>
      </div>
      <div className="text-center">
        <div className="text-xs font-medium text-anthracite-200">{label}</div>
        {valueLabel && <div className="text-[11px] text-anthracite-300">{valueLabel}</div>}
      </div>
    </div>
  );
}
