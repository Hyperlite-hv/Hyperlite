import { useEffect, useRef } from "react";
import { AlertTriangle } from "lucide-react";

export default function ConfirmDialog({ open, title, message, confirmLabel = "Confirm", danger = true, onConfirm, onCancel }) {
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    // Remember what had the focus so that it can be restored, then focus the safe
    // choice (Cancel) so that an accidental Enter never confirms a destructive action.
    const previous = document.activeElement;
    cancelRef.current?.focus();
    function onKeyDown(e) {
      if (e.key === "Escape") { e.stopPropagation(); onCancel(); }
      else if (e.key === "Tab") {
        // Keep the focus inside the dialog (two buttons).
        e.preventDefault();
        (document.activeElement === cancelRef.current ? confirmRef : cancelRef).current?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (previous && typeof previous.focus === "function") previous.focus();
    };
  }, [open, onCancel]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onCancel}>
      <div className="card w-full max-w-sm p-5" role="alertdialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3">
          <div className={`mt-0.5 rounded-full p-1.5 ${danger ? "bg-status-error/15 text-status-error" : "bg-accent-blue/15 text-accent-blue"}`}>
            <AlertTriangle size={18} />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-semibold text-anthracite-100">{title}</h3>
            <p className="mt-1 text-sm text-anthracite-200">{message}</p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button ref={cancelRef} className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button ref={confirmRef} className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
