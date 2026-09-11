// Jauge circulaire (CPU / RAM / stockage). Pur SVG, pas de dependance externe.
export default function GaugeRing({ label, ratio, valueLabel, size = 96, colorClass = "text-accent-blue" }) {
  const clamped = Math.max(0, Math.min(1, ratio ?? 0));
  const stroke = 8;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - clamped);
  const pct = Math.round(clamped * 100);

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} className="stroke-anthracite-600" fill="none" />
          <circle
            cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} fill="none"
            strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
            className={`${colorClass} transition-[stroke-dashoffset] duration-700 ease-out`}
            stroke="currentColor"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-lg font-semibold text-anthracite-100">{pct}%</span>
        </div>
      </div>
      <div className="text-center">
        <div className="text-xs font-medium text-anthracite-200">{label}</div>
        {valueLabel && <div className="text-[11px] text-anthracite-300">{valueLabel}</div>}
      </div>
    </div>
  );
}
