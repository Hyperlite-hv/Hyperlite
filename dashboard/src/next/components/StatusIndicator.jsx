import { stateInfo } from "../lib/enums";
import { useT } from "../i18n";

const SHAPES = {
  dot: <circle cx="6" cy="6" r="5" fill="currentColor" />,
  square: <rect x="1.5" y="1.5" width="9" height="9" rx="1" fill="currentColor" />,
  pause: <g fill="currentColor"><rect x="2" y="1.5" width="3" height="9" rx="0.5" /><rect x="7" y="1.5" width="3" height="9" rx="0.5" /></g>,
  half: <g><circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M6 1.5a4.5 4.5 0 0 1 0 9z" fill="currentColor" /></g>,
  triangle: <path d="M6 1 11.2 10.5H0.8z" fill="currentColor" />,
  diamond: <path d="M6 0.5 11.5 6 6 11.5 0.5 6z" fill="currentColor" />,
  ring: <circle cx="6" cy="6" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.6" />,
  spinner: <circle className="nx-spin" cx="6" cy="6" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray="14 12" style={{ transformOrigin: "6px 6px" }} />,
  check: <path d="M1.5 6.5 4.6 9.5 10.5 2.8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />,
  cross: <path d="M2 2 10 10M10 2 2 10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />,
  arrows: <path d="M1 4h9M8 1.5 10.5 4 8 6.5M11 8H2M4 5.5 1.5 8 4 10.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />,
};

// State = shape + accessible text + colour. `compact` hides the visible label but keeps it
// available to assistive technology.
export default function StatusIndicator({ kind = "vm", wire, override, compact = false, className = "" }) {
  const t = useT();
  const info = override || stateInfo(kind, wire);
  const label = t(info.key);
  return (
    <span className={`nx-state nx-tone-${info.tone} ${className}`} data-state={info.key}>
      <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">{SHAPES[info.shape] || SHAPES.ring}</svg>
      {compact ? <span className="nx-sr">{label}</span> : <span className="nx-state-text">{label}</span>}
    </span>
  );
}
export { SHAPES };
