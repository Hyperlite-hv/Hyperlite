import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { chartColors } from "../theme/colors";
import { useInfraStore } from "../store/useInfraStore";

function formatTime(t) {
  const d = new Date(t);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function MetricChart({ data, series, height = 180, yFormatter }) {
  const theme = useInfraStore((s) => s.theme);
  // Recharts renders its own SVG text and does not pick up Tailwind's dark:
  // variants, so chartColors.axis (tuned to read on the dark card background)
  // fails contrast against the light-mode card: ~2.5:1, under the 4.5:1 AA
  // minimum for 11px tick labels. Swap in the light-mode muted-text color
  // (matches --a-400 in index.css) instead of reusing the fixed hex everywhere.
  const axisColor = theme === "dark" ? chartColors.axis : "#5B6472";
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
          <defs>
            {series.map((s) => (
              <linearGradient key={s.key} id={`grad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={s.color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={s.color} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={chartColors.grid} vertical={false} />
          <XAxis dataKey="t" tickFormatter={formatTime} stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} minTickGap={40} />
          <YAxis stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} tickFormatter={yFormatter} width={40} />
          <Tooltip
            labelFormatter={formatTime}
            formatter={(value, name) => [yFormatter ? yFormatter(value) : value, name]}
            contentStyle={{ background: "#23262f", border: "1px solid #3a3f4b", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "#a4aabb" }}
          />
          {series.map((s) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              fill={`url(#grad-${s.key})`}
              strokeWidth={1.75}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
