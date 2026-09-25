// Ring gauge for a single 0-100% metric (CPU, memory, storage…). Pure SVG, no dependency:
// a track circle plus a foreground arc drawn via stroke-dasharray, value centred inside.
const SIZE = 84;
const STROKE = 7;
const R = (SIZE - STROKE) / 2;
const C = 2 * Math.PI * R;

export default function CircularGauge({ value, label, tone = "accent", size = SIZE }) {
  const known = value != null && !Number.isNaN(value);
  const pct = known ? Math.min(100, Math.max(0, value)) : 0;
  const offset = C - (pct / 100) * C;
  return (
    <div className="nx-gauge" role="img" aria-label={`${label}${known ? `: ${Math.round(pct)}%` : ""}`}>
      <svg width={size} height={size} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
        <circle className="nx-gauge-track" cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" strokeWidth={STROKE} />
        {known && (
          <circle
            className={`nx-gauge-fill nx-tone-${tone}`}
            cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" stroke="currentColor" strokeWidth={STROKE}
            strokeDasharray={C} strokeDashoffset={offset} strokeLinecap="round"
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          />
        )}
      </svg>
      <div className="nx-gauge-value" style={{ width: size, height: size }}>
        {known ? <span>{Math.round(pct)}<small>%</small></span> : <span className="nx-muted">—</span>}
      </div>
    </div>
  );
}
