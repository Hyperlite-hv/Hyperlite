import { useEffect, useRef } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Standard dialog keyboard behavior: focus moves into the dialog when it opens, Tab and
 * Shift+Tab stay inside it, Escape closes it, and the focus returns to the element that
 * opened it. Attach the returned ref to the element that has role="dialog".
 */
export function useModalBehavior(open, onClose) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    const dialog = ref.current;
    const focusables = () => (dialog ? [...dialog.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null) : []);
    const first = focusables().find((el) => el.hasAttribute("autofocus")) ?? focusables()[0];
    first?.focus();
    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "Tab") {
        const items = focusables();
        if (items.length === 0) return;
        const firstEl = items[0];
        const lastEl = items[items.length - 1];
        if (e.shiftKey && (document.activeElement === firstEl || !dialog.contains(document.activeElement))) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && (document.activeElement === lastEl || !dialog.contains(document.activeElement))) {
          e.preventDefault();
          firstEl.focus();
        }
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (previous && typeof previous.focus === "function") previous.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return ref;
}
