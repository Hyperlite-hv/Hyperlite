import { useCallback, useEffect, useState } from "react";
import { CircleStop, Copy, Download } from "lucide-react";
import { cancelTask, fetchTaskLog } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { usePolling } from "../lib/polling";

const running = (task) => task.status === "running" || task.status === "pending";

// The log has no level of its own: the terminal view reads one from the words of each line, for colour only.
export function lineLevel(message) {
  const m = String(message || "");
  if (/\bwith \d+ errors?\b/i.test(m)) return "info";
  if (/\b(error|errors|failed|failure|fatal|refused|denied|timed out)\b/i.test(m)) return "error";
  if (/\b(warn|warning|retry|retrying|not responding|skipped)\b/i.test(m)) return "warn";
  if (/\b(done|finished|completed|succeeded|created|ok)\b/i.test(m)) return "ok";
  return "info";
}
const LEVEL_TEXT = { error: "ERROR", warn: "WARN", ok: "OK", info: "INFO" };

// A task's own log, refreshed while it runs, and the ways to stop it: a clean stop when the task has one, closing
// it when nobody runs it any more, or (administrators) closing the record of one that cannot stop midway.
// tools (the terminal view of the Activity page): find a word in the log, keep its errors and warnings only, copy or
// download it whole, for whoever is working out what went wrong.
export default function TaskLog({ task, onChanged, terminal = false, tools = false }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const admin = useAuthStore((s) => s.role) === "admin";
  const username = useAuthStore((s) => s.username);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [lines, setLines] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [find, setFind] = useState("");
  const [problems, setProblems] = useState(false);
  const load = useCallback(async () => {
    try { setLines(await fetchTaskLog(task.id, task.on)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [task.id, task.on]);
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
      const r = await cancelTask(task.id, copy.force, task.on);
      pushToast({ kind: "success", title: r.result === "requested" ? t("tl.requested") : t("tl.closed"), message: task.target || task.type });
      load(); onChanged?.();
    } catch (e) { pushToast({ kind: "error", title: t("tl.cancelFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }

  const controls = running(task) && mine && (
    <div className="nx-inline nx-tasklog-acts">
      {task.clean_shutdown && <button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={() => stop("cancel")}><CircleStop size={14} aria-hidden="true" />{t("tl.cancel")}</button>}
      {!task.clean_shutdown && task.orphaned && <button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={() => stop("close")}><CircleStop size={14} aria-hidden="true" />{t("tl.close")}</button>}
      {!task.clean_shutdown && !task.orphaned && <span className="nx-muted">{t("tl.noStop")}</span>}
      {!task.clean_shutdown && !task.orphaned && admin && <button type="button" className="nx-btn nx-btn--sm nx-btn--danger" disabled={busy} onClick={() => stop("force")}>{t("tl.force")}</button>}
    </div>
  );

  if (terminal) {
    const all = [...(lines || [])];
    // The task's final error is the last word of its log, when the log itself did not say it ("Failed: <error>" does).
    if (task.error && !all.some((l) => String(l.message).includes(task.error))) all.push({ at: task.finished_at || null, message: task.error, level: "error" });
    const leveled = all.map((l) => ({ ...l, level: l.level || lineLevel(l.message) }));
    const needle = find.trim().toLowerCase();
    const kept = leveled.filter((l) => (!problems || l.level === "error" || l.level === "warn") && (!needle || String(l.message).toLowerCase().includes(needle)));
    // The whole log as text, with its full timestamps: what goes into a ticket or a message to someone who can help.
    const text = () => [`# ${task.type} ${task.target || ""} · ${task.id} · ${task.status}`, ...leveled.map((l) => `${l.at || "-"} ${LEVEL_TEXT[l.level]} ${l.message}`)].join("\n");
    const copy = async () => {
      try { await navigator.clipboard.writeText(text()); pushToast({ kind: "success", title: t("tl.copied") }); }
      catch { pushToast({ kind: "error", title: t("tl.copyFailed") }); }
    };
    const save = () => {
      const url = URL.createObjectURL(new Blob([text() + "\n"], { type: "text/plain" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: `hyperlite-${task.type}-${task.id.slice(0, 8)}.log` });
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    };
    const nProblems = leveled.filter((l) => l.level === "error" || l.level === "warn").length;
    return (
      <div className="nx-tasklog nx-tasklog--term">
        {controls}
        <h3 className="nx-sr">{t("tl.log")}</h3>
        {tools && all.length > 0 && (
          <div className="nx-tlog-tools" role="group" aria-label={t("tl.log")}>
            <span className="nx-tlog-g nx-tlog-g--find">
              <input type="search" className="nx-inp nx-tlog-find" value={find} onChange={(e) => setFind(e.target.value)} placeholder={t("tl.find")} aria-label={t("tl.find")} />
              <button type="button" className="nx-btn nx-btn--sm" aria-pressed={problems} disabled={!nProblems && !problems} onClick={() => setProblems((p) => !p)}>{t("tl.problems", { n: nProblems })}</button>
            </span>
            <span className="nx-tlog-g nx-tlog-g--out">
              <span className="nx-muted nx-tlog-count">{kept.length === all.length ? t("tl.lines", { n: all.length }) : t("tl.linesOf", { n: kept.length, total: all.length })}</span>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={copy}><Copy size={14} aria-hidden="true" />{t("tl.copy")}</button>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={save}><Download size={14} aria-hidden="true" />{t("tl.download")}</button>
            </span>
          </div>
        )}
        {error ? <p className="nx-hint nx-hint--error" role="alert" style={{ margin: 0 }}>{error}</p>
          : lines == null ? <p className="nx-tlog-empty">…</p>
          : all.length === 0 ? <p className="nx-tlog-empty">{t("tl.empty")}</p>
          : kept.length === 0 ? <p className="nx-tlog-empty">{t("tl.noMatch")}</p> : (
            <ol className="nx-tlog" aria-live={running(task) ? "polite" : undefined}>
              {kept.map((l, i) => (
                <li key={i} data-level={l.level}><span className="nx-tlog-t">{l.at ? time(l.at) : "--:--:--"}</span><span className="nx-tlog-l">{LEVEL_TEXT[l.level]}</span><span className="nx-tlog-m">{l.message}</span></li>
              ))}
            </ol>
          )}
      </div>
    );
  }

  return (
    <div className="nx-tasklog">
      {controls}
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
