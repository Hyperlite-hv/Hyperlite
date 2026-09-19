import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { chartColors } from "../theme/colors";

function formatTime(t) {
  const d = new Date(t);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function MetricChart({ data, series, height = 180, yFormatter }) {
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
          <XAxis dataKey="t" tickFormatter={formatTime} stroke={chartColors.axis} fontSize={11} tickLine={false} axisLine={false} minTickGap={40} />
          <YAxis stroke={chartColors.axis} fontSize={11} tickLine={false} axisLine={false} tickFormatter={yFormatter} width={40} />
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
