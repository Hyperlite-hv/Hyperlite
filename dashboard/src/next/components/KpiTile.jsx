import Sparkline from "./Sparkline";
import StatusIndicator from "./StatusIndicator";
import { useT } from "../i18n";

// One metric: label, big value, level (text + shape + colour past 80 % / 90 %), trend.
export default function KpiTile({ label, value, sub, ratio, series, unavailable }) {
  const t = useT();
  const level = ratio == null ? null : ratio >= 0.9 ? { key: "ns.level.critical", shape: "diamond", tone: "danger" } : ratio >= 0.8 ? { key: "ns.level.high", shape: "triangle", tone: "warning" } : null;
  return (
    <div className="nx-card nx-kpi">
      <div className="nx-muted" style={{ fontSize: "var(--font-size-xs)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      {unavailable ? (
        <div className="nx-muted" style={{ minHeight: "3.5rem" }} role="status">{unavailable}</div>
      ) : (
        <>
          <div className="nx-kpi-value">{value ?? "—"}{level && <span style={{ marginLeft: 8, fontSize: "var(--font-size-sm)" }}><StatusIndicator override={level} /></span>}</div>
          <div className="nx-muted nx-mono" style={{ fontSize: "var(--font-size-xs)" }}>{sub || " "}</div>
          {ratio != null && <div className="nx-progress" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(ratio * 100)} style={{ marginTop: 4 }}><span style={{ width: `${Math.min(100, Math.round(ratio * 100))}%`, background: ratio >= 0.9 ? "var(--color-danger)" : ratio >= 0.8 ? "var(--color-warning)" : "var(--color-accent)" }} /></div>}
          {series !== null && <Sparkline values={series} label={label} tone={level?.tone || "accent"} max={ratio != null ? 100 : null} />}
          <span className="nx-sr">{t("ns.trendHint")}</span>
        </>
      )}
    </div>
  );
}
