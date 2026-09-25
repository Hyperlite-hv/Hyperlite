import Sparkline from "./Sparkline";

// A metric that is a number, not a percentage (counts, throughput): label, big value, detail, trend.
export default function StatTile({ label, value, sub, series, tone = "accent", unavailable, onClick }) {
  const body = (
    <>
      <div className="nx-muted" style={{ fontSize: "var(--font-size-xs)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      {unavailable ? <div className="nx-muted" role="status" style={{ minHeight: "3rem" }}>{unavailable}</div> : (
        <>
          <div className="nx-kpi-value">{value ?? "—"}</div>
          {sub && <div className="nx-muted nx-mono" style={{ fontSize: "var(--font-size-xs)" }}>{sub}</div>}
          {series !== undefined && series !== null && <Sparkline values={series} label={label} tone={tone} />}
        </>
      )}
    </>
  );
  return onClick
    ? <button type="button" className="nx-card nx-kpi nx-stat nx-stat--link" onClick={onClick}>{body}</button>
    : <div className="nx-card nx-kpi nx-stat">{body}</div>;
}
