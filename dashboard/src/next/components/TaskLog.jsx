import { useCallback, useEffect, useState } from "react";
import { CircleStop } from "lucide-react";
import { cancelTask, fetchTaskLog } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { usePolling } from "../lib/polling";

const running = (task) => task.statut === "en_cours" || task.statut === "en_attente";

// A task's own log, refreshed while it runs, and the ways to stop it: a clean stop when the task has one, closing
// it when nobody runs it any more, or (administrators) closing the record of one that cannot stop midway.
export default function TaskLog({ task, onChanged }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const admin = useAuthStore((s) => s.role) === "admin";
  const username = useAuthStore((s) => s.username);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [lines, setLines] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setLines(await fetchTaskLog(task.id)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [task.id]);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 2000, { enabled: running(task) });

  const mine = admin || task.username === username;
  const time = (iso) => new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(iso));

  async function stop(kind) {
    const copy = {
      cancel: { title: t("tl.cancelTitle"), message: t("tl.cancelMsg"), confirm: t("tl.cancel"), danger: false, force: false },
      close: { title: t("tl.closeTitle"), message: t("tl.closeMsg"), confirm: t("tl.close"), danger: false, force: false },
      force: { title: t("tl.forceTitle"), message: t("tl.forceMsg"), confirm: t("tl.force"), danger: true, force: true },
    }[kind];
    if (!(await confirmAction({ title: copy.title, message: copy.message, confirmLabel: copy.confirm, danger: copy.danger }))) return;
    setBusy(true);
    try {
      const r = await cancelTask(task.id, copy.force);
      pushToast({ kind: "success", title: r.resultat === "requested" ? t("tl.requested") : t("tl.closed"), message: task.cible || task.type });
      load(); onChanged?.();
    } catch (e) { pushToast({ kind: "error", title: t("tl.cancelFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }

  return (
    <div className="nx-tasklog">
      {running(task) && mine && (
        <div className="nx-inline" style={{ marginBottom: "var(--space-2)" }}>
          {task.arret_propre && <button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={() => stop("cancel")}><CircleStop size={14} aria-hidden="true" />{t("tl.cancel")}</button>}
          {!task.arret_propre && task.orpheline && <button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={() => stop("close")}><CircleStop size={14} aria-hidden="true" />{t("tl.close")}</button>}
          {!task.arret_propre && !task.orpheline && <span className="nx-muted">{t("tl.noStop")}</span>}
          {!task.arret_propre && !task.orpheline && admin && <button type="button" className="nx-btn nx-btn--sm nx-btn--danger" disabled={busy} onClick={() => stop("force")}>{t("tl.force")}</button>}
        </div>
      )}
      <h3 className="nx-tasklog-h">{t("tl.log")}</h3>
      {error ? <p className="nx-hint nx-hint--error" role="alert" style={{ margin: 0 }}>{error}</p>
        : lines == null ? <p className="nx-muted" style={{ margin: 0 }}>…</p>
        : lines.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("tl.empty")}</p> : (
          <ol className="nx-tasklog-l" aria-live={running(task) ? "polite" : undefined}>
            {lines.map((l, i) => <li key={i}><span className="nx-mono nx-muted">{time(l.at)}</span> {l.message}</li>)}
          </ol>
        )}
    </div>
  );
}
