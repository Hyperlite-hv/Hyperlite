import { Card } from "@/components/ui/card";

// Statistics tile (header of the Datacenter dashboard): a round tinted badge + a
// big number, reusable wherever this pattern makes sense (only
// DatacenterSummaryTab for now). Optionally clickable (onClick): navigates to
// the resource it summarizes, with a hover/focus affordance so it reads as a
// link rather than a static number.
const TONES = {
  blue: "bg-accent-blue/10 text-accent-blue",
  green: "bg-status-running/10 text-status-running",
  gray: "bg-secondary text-secondary-foreground",
  amber: "bg-status-warning/10 text-status-warning",
};

export default function StatTile({ icon: Icon, label, value, foot, tone = "blue", onClick }) {
  return (
    <Card
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={onClick ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(e); } } : undefined}
      className={`flex-row items-center gap-3.5 p-4 ${onClick ? "cursor-pointer transition-colors duration-150 hover:bg-muted/40" : ""}`}
    >
      <div className={`flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-full ${TONES[tone] || TONES.blue}`}>
        <Icon size={22} />
      </div>
      <div className="min-w-0">
        <div className="truncate text-[12px] font-semibold text-muted-foreground">{label}</div>
        <div className="font-mono text-[24px] font-extrabold leading-tight text-foreground">{value}</div>
        {foot && <div className="truncate text-[11px] text-muted-foreground">{foot}</div>}
      </div>
    </Card>
  );
}
