import CircularGauge from "./CircularGauge";
import StatusIndicator from "./StatusIndicator";
import { useT } from "../i18n";

// One metric as a ring gauge: a fixed identity colour per metric (tone), escalated to
// warning/danger past 80 % / 90 % regardless of that identity colour.
export default function KpiTile({ label, sub, ratio, tone = "accent", unavailable }) {
  const t = useT();
  const level = ratio == null ? null : ratio >= 0.9 ? { key: "ns.level.critical", shape: "diamond", tone: "danger" } : ratio >= 0.8 ? { key: "ns.level.high", shape: "triangle", tone: "warning" } : null;
  const ringTone = level?.tone || tone;
  return (
    <div className="nx-card nx-kpi nx-kpi--ring">
      {unavailable ? (
        <>
          <div className="nx-gauge nx-gauge--empty" aria-hidden="true"><span className="nx-muted">—</span></div>
          <div className="nx-kpi-label">{label}</div>
          <div className="nx-muted" style={{ fontSize: "var(--font-size-xs)" }} role="status">{unavailable}</div>
        </>
      ) : (
        <>
          <CircularGauge value={ratio != null ? ratio * 100 : null} label={label} tone={ringTone} size={72} />
          <div className="nx-kpi-label">{label}{level && <StatusIndicator override={level} compact />}</div>
          {sub && <div className="nx-muted nx-mono" style={{ fontSize: "var(--font-size-xs)" }}>{sub}</div>}
          <span className="nx-sr">{t("ns.trendHint")}</span>
        </>
      )}
    </div>
  );
}
