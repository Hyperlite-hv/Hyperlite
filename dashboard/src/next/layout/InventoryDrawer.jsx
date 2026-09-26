import { useEffect, useRef } from "react";
import Explorer from "../explorer/Explorer";
import { useT } from "../i18n";

// The Inventory Explorer (Server / Pool modes, perspectives, search, keyboard tree) lives in a panel that opens
// next to the sidebar from its cluster button, instead of taking room in the navigation column. It closes on
// Escape, on a click outside and after a navigation.
export default function InventoryDrawer({ open, onClose }) {
  const t = useT();
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      // In the search box, Escape first clears the text (handled by the explorer); a second one closes the panel.
      if (e.target instanceof HTMLInputElement && e.target.closest(".nx-search") && e.target.value) return;
      e.stopPropagation(); onClose();
    };
    // capture phase: read the search text before the explorer clears it
    window.addEventListener("keydown", onKey, true);
    // Focus the search so typing filters at once.
    const id = requestAnimationFrame(() => ref.current?.querySelector(".nx-search input")?.focus());
    return () => { window.removeEventListener("keydown", onKey, true); cancelAnimationFrame(id); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="nx-inv-scrim" onClick={onClose} aria-hidden="true" />
      <aside ref={ref} className="nx-inv-drawer" role="dialog" aria-label={t("inv.title")}>
        <div className="nx-inv-drawer-head">
          <strong>{t("inv.title")}</strong>
          <button type="button" className="nx-btn nx-btn--icon nx-btn--ghost" aria-label={t("inv.closePanel")} onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          </button>
        </div>
        <Explorer open onNavigate={() => { window.dispatchEvent(new Event("nx:navigated")); onClose(); }} onCreateVm={() => { onClose(); window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" })); }} />
      </aside>
    </>
  );
}
