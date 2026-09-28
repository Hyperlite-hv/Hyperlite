import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Menu from "./Menu";

// Right-click menu: the same Menu as the Actions buttons (arrow keys, Escape, click outside, focus back to the row),
// opened at the pointer and kept inside the window. Portalled into the app root so a table's overflow or a sticky
// band never clips it. Scrolling or resizing closes it, like a system menu.
export default function ContextMenu({ at, label, onClose, returnFocus, children }) {
  const box = useRef(null);
  const focusRef = useRef(returnFocus || null);
  const [pos, setPos] = useState(at);

  useLayoutEffect(() => {
    const el = box.current?.firstElementChild;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const x = Math.max(8, Math.min(at.x, window.innerWidth - r.width - 8));
    const y = Math.max(8, Math.min(at.y, window.innerHeight - r.height - 8));
    setPos((p) => (p.x === x && p.y === y ? p : { x, y }));
  }, [at]);

  useEffect(() => {
    const shut = () => onClose();
    window.addEventListener("resize", shut);
    window.addEventListener("scroll", shut, true);
    return () => { window.removeEventListener("resize", shut); window.removeEventListener("scroll", shut, true); };
  }, [onClose]);

  return createPortal(
    <div ref={box} onContextMenu={(e) => e.preventDefault()}>
      <Menu open onClose={onClose} label={label} returnFocusRef={focusRef} style={{ position: "fixed", left: pos.x, top: pos.y, zIndex: 60 }}>
        {children}
      </Menu>
    </div>,
    document.querySelector(".nx-root") || document.body,
  );
}

// Where the menu opens: at the pointer for a right click; under the start of the row when the keyboard asked for it
// (the Menu key or Shift+F10 fire the same event, without pointer coordinates).
export function contextPoint(e) {
  if (e.clientX || e.clientY) return { x: e.clientX, y: e.clientY };
  const r = e.currentTarget.getBoundingClientRect();
  return { x: r.left + 24, y: r.top + Math.min(r.height, 36) };
}

// State of a list's right-click menu: `open(kind, object)` is the row's onContextMenu handler.
export function useContextTarget() {
  const [target, setTarget] = useState(null);
  const open = (kind, obj, key = obj?.nom ?? obj?.id) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    setTarget({ kind, obj, key, at: contextPoint(e), el: e.currentTarget });
  };
  // is(kind, key): the row this menu belongs to, highlighted while it is open ("is-ctx").
  const is = (kind, key) => target?.kind === kind && target.key === key;
  return { target, open, close: () => setTarget(null), is };
}
