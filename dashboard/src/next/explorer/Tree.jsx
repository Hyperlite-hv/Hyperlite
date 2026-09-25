import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import StatusIndicator from "../components/StatusIndicator";
import { highlight } from "./buildTree";
import { normalize } from "../lib/format";
import { useT } from "../i18n";

const WINDOW_THRESHOLD = 250;
const OVERSCAN = 12;

function Chevron({ open }) {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform var(--duration-fast)" }}>
      <path d="M3 1.5 7 5 3 8.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Label({ text, query }) {
  const parts = highlight(text, query);
  return <span className="nx-label">{parts.map((p, i) => (typeof p === "string" ? p : <mark key={i} className="nx-hit" style={{ color: "inherit" }}>{p.hit}</mark>))}</span>;
}

function rowName(item, t) {
  const { row } = item;
  const bits = [row.label];
  if (row.kind === "vm") bits.push(t("res.vm").toLowerCase());
  else if (row.kind === "node") bits.push(t("res.node").toLowerCase());
  else if (row.kind === "container") bits.push(t("res.container").toLowerCase());
  if (row.info) bits.push(t(row.info.key));
  if (row.sub) bits.push(row.sub);
  return bits.join(", ");
}

// Flat ARIA tree (role=tree > treeitem with aria-level/posinset/setsize): one tab stop
// (roving tabindex), full keyboard model of the WAI-ARIA tree view pattern, optional windowing.
export default function Tree({ flat, selectionKey, focusKey, setFocusKey, onActivate, onToggle, onContext, query, density, label }) {
  const t = useT();
  const boxRef = useRef(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(600);
  const typeahead = useRef({ buf: "", timer: null });
  const rowH = density === "compact" ? 24 : 28;
  const windowed = flat.length > WINDOW_THRESHOLD;

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    setViewH(el.clientHeight || 600);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => setViewH(el.clientHeight || 600)) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, []);

  const indexByKey = useMemo(() => new Map(flat.map((f, i) => [f.row.key, i])), [flat]);
  const focusIndex = indexByKey.has(focusKey) ? indexByKey.get(focusKey) : 0;
  const tabKey = flat[focusIndex]?.row.key;

  const start = windowed ? Math.max(0, Math.floor(scrollTop / rowH) - OVERSCAN) : 0;
  const end = windowed ? Math.min(flat.length, Math.ceil((scrollTop + viewH) / rowH) + OVERSCAN) : flat.length;
  const slice = flat.slice(start, end);

  // Keep the keyboard-focused row visible and DOM-focused after key navigation.
  const wantFocus = useRef(false);
  useEffect(() => {
    if (!wantFocus.current) return;
    const el = boxRef.current;
    if (!el) return;
    const top = focusIndex * rowH;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + rowH > el.scrollTop + el.clientHeight) el.scrollTop = top + rowH - el.clientHeight;
    const node = el.querySelector(`[data-key="${CSS.escape(tabKey || "")}"]`);
    node?.focus();
    wantFocus.current = false;
  });

  function go(i) {
    const item = flat[Math.max(0, Math.min(flat.length - 1, i))];
    if (!item) return;
    wantFocus.current = true;
    setFocusKey(item.row.key);
  }

  function onKeyDown(e) {
    if (e.target !== e.currentTarget && !e.target.closest?.("[role=treeitem]")) return;
    const item = flat[focusIndex];
    if (!item) return;
    const { row } = item;
    const isOpen = item.expandable && flat[focusIndex + 1]?.parentKey === row.key;
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); go(focusIndex + 1); break;
      case "ArrowUp": e.preventDefault(); go(focusIndex - 1); break;
      case "Home": e.preventDefault(); go(0); break;
      case "End": e.preventDefault(); go(flat.length - 1); break;
      case "ArrowRight":
        e.preventDefault();
        if (item.expandable && !isOpen) onToggle(row, true, item.level);
        else if (isOpen) go(focusIndex + 1);
        break;
      case "ArrowLeft":
        e.preventDefault();
        if (isOpen) onToggle(row, false, item.level);
        else if (item.parentKey) go(indexByKey.get(item.parentKey));
        break;
      case "Enter": case " ": e.preventDefault(); onActivate(row, item); break;
      case "*":
        e.preventDefault();
        flat.filter((f) => f.parentKey === item.parentKey && f.expandable).forEach((f) => onToggle(f.row, true, f.level));
        break;
      case "ContextMenu": e.preventDefault(); openContext(row, e.currentTarget.querySelector(`[data-key="${CSS.escape(row.key)}"]`)); break;
      case "F10": if (e.shiftKey) { e.preventDefault(); openContext(row, e.currentTarget.querySelector(`[data-key="${CSS.escape(row.key)}"]`)); } break;
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== "/") {
          const ta = typeahead.current;
          clearTimeout(ta.timer);
          ta.buf += normalize(e.key);
          ta.timer = setTimeout(() => { ta.buf = ""; }, 600);
          const from = ta.buf.length === 1 ? focusIndex + 1 : focusIndex;
          for (let k = 0; k < flat.length; k++) {
            const idx = (from + k) % flat.length;
            if (normalize(flat[idx].row.label).startsWith(ta.buf)) { go(idx); break; }
          }
        }
    }
  }

  function openContext(row, el) {
    const r = el?.getBoundingClientRect?.();
    onContext?.(row, { x: r ? r.left + 24 : 0, y: r ? r.bottom : 0, el });
  }

  return (
    <div
      ref={boxRef}
      className="nx-tree"
      role="tree"
      aria-label={label}
      data-density={density}
      onKeyDown={onKeyDown}
      onScroll={windowed ? (e) => setScrollTop(e.currentTarget.scrollTop) : undefined}
    >
      <div style={windowed ? { height: flat.length * rowH, position: "relative" } : undefined}>
        <div style={windowed ? { position: "absolute", top: start * rowH, left: 0, right: 0 } : undefined}>
          {slice.map((item) => {
            const { row, level } = item;
            const isOpen = item.expandable && flat[indexByKey.get(row.key) + 1]?.parentKey === row.key;
            const selected = row.key === selectionKey;
            return (
              <div
                key={row.key}
                data-key={row.key}
                role="treeitem"
                aria-level={level}
                aria-setsize={item.setsize}
                aria-posinset={item.posinset}
                aria-expanded={item.expandable ? !!isOpen : undefined}
                aria-selected={selected}
                aria-label={rowName(item, t)}
                tabIndex={row.key === tabKey ? 0 : -1}
                className="nx-row"
                data-offline={row.kind === "node" && row.info?.tone === "offline" ? "true" : undefined}
                style={{ paddingLeft: 6 + (level - 1) * 14 }}
                onFocus={() => setFocusKey(row.key)}
                onClick={() => onActivate(row, item)}
                onContextMenu={(e) => { if (onContext) { e.preventDefault(); onContext(row, { x: e.clientX, y: e.clientY }); } }}
              >
                {item.expandable ? (
                  <button type="button" tabIndex={-1} className="nx-chev" aria-hidden="true" onClick={(e) => { e.stopPropagation(); onToggle(row, !isOpen, level); }}>
                    <Chevron open={isOpen} />
                  </button>
                ) : <span className="nx-chev nx-chev--none" />}
                {row.info && <StatusIndicator override={row.info} compact />}
                <Label text={row.label} query={query} />
                {row.badge && <span className="nx-kbd" style={{ marginLeft: 4 }}>{row.badge}</span>}
                {row.sub && <span className="nx-sub">{row.sub}</span>}
                {row.kind === "category" && <span className="nx-count">{row.count}</span>}
                {(row.kind === "dc" || row.kind === "node" || row.kind === "category") && (row.problems?.danger > 0 || row.problems?.warning > 0) && (
                  <span className="nx-agg" aria-hidden="true">
                    {row.problems.warning > 0 && <span className="nx-tone-warning">▲ {row.problems.warning}</span>}
                    {row.problems.danger > 0 && <span className="nx-tone-danger">◆ {row.problems.danger}</span>}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
