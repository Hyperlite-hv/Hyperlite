import { useEffect, useRef } from "react";

// Accessible popup menu: role=menu, arrow keys, Home/End, Escape, click-outside, focus return.
export default function Menu({ open, onClose, style, label, children, returnFocusRef }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const items = () => [...ref.current.querySelectorAll('[role="menuitem"]:not([aria-disabled="true"])')];
    items()[0]?.focus();
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", onDoc);
    const restore = returnFocusRef?.current;
    return () => {
      document.removeEventListener("mousedown", onDoc);
      if (restore && document.body.contains(restore)) restore.focus();
    };
  }, [open, onClose, returnFocusRef]);

  if (!open) return null;

  function onKeyDown(e) {
    const list = [...ref.current.querySelectorAll('[role="menuitem"]:not([aria-disabled="true"])')];
    const i = list.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length]?.focus(); }
    else if (e.key === "Home") { e.preventDefault(); list[0]?.focus(); }
    else if (e.key === "End") { e.preventDefault(); list[list.length - 1]?.focus(); }
    else if (e.key === "Tab") onClose();
  }

  return (
    <div ref={ref} className="nx-menu" role="menu" aria-label={label} style={style} onKeyDown={onKeyDown}>
      {children}
    </div>
  );
}

export function MenuItem({ children, onSelect, disabled, reason, danger, title }) {
  return (
    <button
      type="button"
      role="menuitem"
      className={danger ? "nx-danger" : undefined}
      aria-disabled={disabled ? "true" : undefined}
      title={disabled && reason ? reason : title}
      onClick={() => { if (!disabled) onSelect?.(); }}
    >
      {children}
      {disabled && reason && <span className="nx-sr"> — {reason}</span>}
    </button>
  );
}
