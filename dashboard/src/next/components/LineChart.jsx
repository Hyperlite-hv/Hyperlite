import { useLayoutEffect, useMemo, useRef, useState } from "react";

const BASE_PAD = { l: 34, r: 10, t: 10, b: 24 };
const COLORS = { info: "var(--color-info)", info2: "var(--color-accent-2)", accent: "var(--color-accent)", warning: "var(--color-warning)", muted: "var(--color-text-muted)" };

// Rounds a data maximum up to a readable axis top (1, 2, 2.5, 5 x 10^n); with base 1024 the rounding happens in the
// displayed unit (Ko/s, Mo/s...), so the axis reads 500 Ko/s rather than 488 Ko/s.
function niceMax(v, base = 10) {
  if (!(v > 0)) return 1;
  const k = base === 1024 ? Math.max(0, Math.floor(Math.log(v) / Math.log(1024))) : 0;
  const unit = base === 1024 ? 1024 ** k : 1;
  const x = v / unit;
  const p = 10 ** Math.floor(Math.log10(x));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= x) * unit;
}

// Small dependency-free line chart, three grid lines, time ticks, hover read-out. The scale is fixed at 0-100 for
// percentages; `max="auto"` fits it to the data (throughputs) and `format` renders values and axis labels.
// It carries a text summary for assistive technology (min / average / max per series) instead of hundreds of points.
export default function LineChart({ series, label, formatTime, unit = "%", height = 210, max = 100, format, base = 10 }) {
  const H = height;
  const PAD = max === "auto" ? { ...BASE_PAD, l: 64 } : BASE_PAD;
  const fmt = format || ((v) => `${Math.round(v)}${unit}`);
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
  const top = max === "auto" ? niceMax(Math.max(0, ...all.map((p) => p.v)), base) : max;
  const y = (v) => PAD.t + (1 - Math.min(top, Math.max(0, v)) / top) * (H - PAD.t - PAD.b);
  const axis = (g) => (max === "auto" ? (format ? format(g, true) : String(g)) : String(g));
  const ticks = useMemo(() => (all.length ? [0, 0.25, 0.5, 0.75, 1].map((f) => t0 + f * (t1 - t0)) : []), [all.length, t0, t1]);
  const summary = series.map((s) => {
    const v = s.points.map((p) => p.v);
    if (!v.length) return `${s.label}: —`;
    return `${s.label}: min ${fmt(Math.min(...v))}, ${fmt(v.reduce((a, b) => a + b, 0) / v.length)} avg, max ${fmt(Math.max(...v))}`;
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
        {[0, top / 2, top].map((g) => (
          <g key={g}><line x1={PAD.l} x2={W - PAD.r} y1={y(g)} y2={y(g)} stroke="var(--color-border-subtle)" strokeWidth="1" /><text x={PAD.l - 6} y={y(g) + 3} textAnchor="end" className="nx-chart-tick">{axis(g)}</text></g>
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
        {hover != null && <span className="nx-mono nx-chart-read" aria-hidden="true">{formatTime(hover)} · {at.map(({ s, p }) => `${s.label} ${fmt(p.v)}`).join(" · ")}</span>}
      </figcaption>
    </figure>
  );
}
