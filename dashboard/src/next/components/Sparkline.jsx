import { useT } from "../i18n";

// Tiny trend chart. Not decorative: it carries an accessible summary (min, max, latest) and a
// text fallback when there are too few points to draw a trend.
export default function Sparkline({ values, label, tone = "accent", max = null, width = 120, height = 28 }) {
  const t = useT();
  const pts = (values || []).filter((v) => v != null && !Number.isNaN(v));
  if (pts.length < 2) return <div className="nx-spark nx-muted" role="img" aria-label={`${label}: ${t("ns.collecting")}`}><span aria-hidden="true">{t("ns.collecting")}</span></div>;
  const lo = Math.min(...pts), hi = max ?? Math.max(...pts);
  const span = hi - (max != null ? 0 : lo) || 1;
  const base = max != null ? 0 : lo;
  const x = (i) => (i / (pts.length - 1)) * (width - 2) + 1;
  const y = (v) => height - 2 - ((v - base) / span) * (height - 4);
  const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1).toFixed(1)} ${height} L1 ${height} Z`;
  const trend = pts[pts.length - 1] - pts[0];
  return (
    <svg className={`nx-spark nx-tone-${tone}`} viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img"
      aria-label={`${label}: ${t("ns.trend", { min: Math.round(Math.min(...pts)), max: Math.round(Math.max(...pts)), last: Math.round(pts[pts.length - 1]), dir: trend > 1 ? t("ns.up") : trend < -1 ? t("ns.down") : t("ns.flat") })}`}>
      <path d={area} fill="currentColor" opacity="0.14" />
      <path d={line} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
