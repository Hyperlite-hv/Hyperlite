import { ChevronRight } from "lucide-react";
import { Card } from "@/components/ui/card";

// Statistics tile (header of the Datacenter dashboard): a round tinted badge + a
// big number, reusable wherever this pattern makes sense (only
// DatacenterSummaryTab for now). Optionally clickable (onClick): navigates to
// the resource it summarizes. When it is, a visible affordance (border glow,
// lift, chevron) marks it as a link — otherwise it reads as a static number,
// so the two must not look alike.
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
      className={`group/tile relative flex-row items-center gap-3.5 p-4 ${
        onClick
          ? "cursor-pointer transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:ring-accent-blue/50 focus-visible:-translate-y-0.5 focus-visible:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/50"
          : ""
      }`}
    >
      <div className={`flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-full ${TONES[tone] || TONES.blue}`}>
        <Icon size={22} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px] font-semibold text-muted-foreground">{label}</div>
        <div className="font-mono text-[24px] font-extrabold leading-tight text-foreground">{value}</div>
        {foot && <div className="truncate text-[11px] text-muted-foreground">{foot}</div>}
      </div>
      {onClick && (
        <ChevronRight
          size={16}
          className="shrink-0 text-muted-foreground opacity-0 -translate-x-1 transition-[opacity,transform] duration-200 group-hover/tile:opacity-100 group-hover/tile:translate-x-0 group-focus-visible/tile:opacity-100 group-focus-visible/tile:translate-x-0"
        />
      )}
    </Card>
  );
}
