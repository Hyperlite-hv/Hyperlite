import { CheckCircle2, XCircle, X } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

export default function ToastContainer() {
  const toasts = useInfraStore((s) => s.toasts);
  const dismissToast = useInfraStore((s) => s.dismissToast);

  return (
    <div className="fixed bottom-4 right-4 z-100 flex flex-col gap-2 w-80">
      {toasts.map((t) => (
        <div
          key={t.id}
          className="card toast-enter flex items-start gap-2.5 p-3 border-l-2"
          style={{ borderLeftColor: t.kind === "error" ? "#e5484d" : "#3fb950" }}
        >
          {t.kind === "error"
            ? <XCircle size={18} className="text-status-error shrink-0 mt-0.5" />
            : <CheckCircle2 size={18} className="text-status-running shrink-0 mt-0.5" />}
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-anthracite-100">{t.title}</div>
            {t.message && <div className="text-xs text-anthracite-300 truncate">{t.message}</div>}
          </div>
          <button aria-label="Close" onClick={() => dismissToast(t.id)} className="text-anthracite-400 hover:text-anthracite-100">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
