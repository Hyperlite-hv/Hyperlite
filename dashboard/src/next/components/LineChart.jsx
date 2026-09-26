import { useLayoutEffect, useMemo, useRef, useState } from "react";

const PAD = { l: 34, r: 10, t: 10, b: 24 };
const COLORS = { info: "var(--color-info)", info2: "var(--color-accent-2)", accent: "var(--color-accent)", warning: "var(--color-warning)", muted: "var(--color-text-muted)" };

// Small dependency-free line chart: fixed 0-100 scale (percentages), three grid lines, time ticks, hover read-out.
// It carries a text summary for assistive technology (min / average / max per series) instead of hundreds of points.
export default function LineChart({ series, label, formatTime, unit = "%", height = 210 }) {
  const H = height;
  const [hover, setHover] = useState(null);
  // The viewBox follows the real width so text keeps its size in narrow and wide cards alike.
  const box = useRef(null);
  const [W, setW] = useState(640);
  useLayoutEffect(() => {
    const el = box.current; if (!el) return undefined;
    const set = () => setW(Math.max(240, Math.round(el.getBoundingClientRect().width)));
    set();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(set) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, []);
  const all = series.flatMap((s) => s.points);
  const t0 = all.length ? Math.min(...all.map((p) => p.t)) : 0;
  const t1 = all.length ? Math.max(...all.map((p) => p.t)) : 1;
  const x = (t) => PAD.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + (1 - Math.min(100, Math.max(0, v)) / 100) * (H - PAD.t - PAD.b);
  const ticks = useMemo(() => (all.length ? [0, 0.25, 0.5, 0.75, 1].map((f) => t0 + f * (t1 - t0)) : []), [all.length, t0, t1]);
  const summary = series.map((s) => {
    const v = s.points.map((p) => p.v);
    if (!v.length) return `${s.label}: —`;
    return `${s.label}: min ${Math.round(Math.min(...v))}${unit}, ${Math.round(v.reduce((a, b) => a + b, 0) / v.length)}${unit} avg, max ${Math.round(Math.max(...v))}${unit}`;
  }).join(" · ");

  function onMove(e) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const t = t0 + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (t1 - t0);
    let best = null;
    for (const p of all) if (!best || Math.abs(p.t - t) < Math.abs(best - t)) best = p.t;
    setHover(best);
  }
  const at = hover == null ? [] : series.map((s) => ({ s, p: s.points.reduce((b, p) => (!b || Math.abs(p.t - hover) < Math.abs(b.t - hover) ? p : b), null) })).filter((r) => r.p);

  return (
    <figure className="nx-chart" style={{ margin: 0 }} ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}. ${summary}`} style={{ width: "100%", height: "auto", display: "block" }} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {[0, 50, 100].map((g) => (
          <g key={g}><line x1={PAD.l} x2={W - PAD.r} y1={y(g)} y2={y(g)} stroke="var(--color-border-subtle)" strokeWidth="1" /><text x={PAD.l - 6} y={y(g) + 3} textAnchor="end" className="nx-chart-tick">{g}</text></g>
        ))}
        {ticks.map((tk, i) => <text key={i} x={x(tk)} y={H - 6} textAnchor={i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"} className="nx-chart-tick">{formatTime(tk)}</text>)}
        {series.map((s) => s.points.length > 1 && (
          <polyline key={s.key} fill="none" stroke={COLORS[s.tone] || COLORS.info} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" opacity={s.faded ? 0.55 : 1} points={s.points.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ")} />
        ))}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b} stroke="var(--color-border-strong)" strokeDasharray="3 3" />}
        {at.map(({ s, p }) => <circle key={s.key} cx={x(p.t)} cy={y(p.v)} r="3.5" fill={COLORS[s.tone] || COLORS.info} stroke="var(--color-bg-surface)" strokeWidth="1.5" />)}
      </svg>
      <figcaption className="nx-chart-legend">
        {series.map((s) => <span key={s.key}><i style={{ background: COLORS[s.tone] || COLORS.info }} aria-hidden="true" />{s.label}</span>)}
        {hover != null && <span className="nx-mono nx-chart-read" aria-hidden="true">{formatTime(hover)} · {at.map(({ s, p }) => `${s.label} ${Math.round(p.v)}${unit}`).join(" · ")}</span>}
      </figcaption>
    </figure>
  );
}
