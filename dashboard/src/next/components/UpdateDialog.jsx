import { useEffect, useRef, useState } from "react";
import { applyUpdate, applyUpdateFile, deleteUpdateFile, fetchHealth, fetchTaskDetail, fetchUpdateCheck, fetchUpdateFile, rollbackUpdate, uploadUpdateFile } from "../../api/client";
import { confirmAction } from "../../store/useConfirmStore";
import { useInfraStore } from "../../store/useInfraStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";

// One update per node (docs/design/updates-1.0.md): Hyperlite and the node's Debian packages together, with the
// release notes and, before anything starts, whether the node will need a reboot. Running VMs are not stopped.
// A node without network updates from a file instead: the one published next to the ISO, checked by the node.
const STEPS = [[5, "upd.step.backup"], [20, "upd.step.index"], [35, "upd.step.reinstall"], [40, "upd.step.install"], [85, "upd.step.restart"]];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

export default function UpdateDialog({ onClose }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | updating | restarting | ok | failed
  const [progress, setProgress] = useState(0);
  const [after, setAfter] = useState(null); // the check once the update is done: is a reboot needed now?
  const [restarted, setRestarted] = useState(false); // Hyperlite itself changed: the page must be reloaded
  const [fromFile, setFromFile] = useState(false); // the "from a file" panel is open
  const [file, setFile] = useState(null); // the update file received by the node: {version, notes, plus_ancienne…}
  const [sending, setSending] = useState(null); // upload progress, 0-100, while a file is sent
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  const dialog = useRef(null);
  const busy = phase === "updating" || phase === "restarting";

  // The dialog itself takes the focus at once: Escape closes it even while the check is still running.
  useEffect(() => { dialog.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);
  useEffect(() => {
    let alive = true;
    fetchUpdateCheck(lang).then((r) => alive && setInfo(r)).catch((e) => alive && setError(errorMessage(e)));
    // A file already sent and not applied yet (the dialog was closed meanwhile).
    fetchUpdateFile(lang).then((f) => { if (alive && f?.version) { setFile(f); setFromFile(true); } }).catch(() => {});
    return () => { alive = false; };
  }, [lang]);

  async function send(chosen) {
    if (!chosen) return;
    setError(null);
    setFile(null);
    setSending(0);
    try { setFile(await uploadUpdateFile(chosen, lang, setSending)); }
    catch (e) { setError(errorMessage(e)); }
    finally { setSending(null); }
  }

  async function dropFile() {
    try { await deleteUpdateFile(); setFile(null); } catch (e) { setError(errorMessage(e)); }
  }

  const sys = info?.system;
  const hyperliteNew = Boolean(info?.verifiable && !info.up_to_date);
  const debianNew = (sys?.packages ?? 0) > 0;
  const nothing = info?.verifiable && !hyperliteNew && !debianNew;

  async function back() {
    if (!(await confirmAction({ title: t("upd.backTitle", { version: info.previous }), message: t("upd.backMsg"), confirmLabel: t("upd.back", { version: info.previous }) }))) return;
    start("rollback");
  }

  // kind: "online" (the repositories), "rollback" (the version kept on the node) or "file" (the update file).
  async function start(kind = "online") {
    setPhase("updating");
    setError(null);
    const target = kind === "rollback" ? info.previous : kind === "file" ? file.version : info.remote_commit;
    const restarts = kind === "rollback" || (kind === "file" ? file.version !== file.installed : hyperliteNew);
    try {
      const { task_id } = kind === "rollback" ? await rollbackUpdate() : kind === "file" ? await applyUpdateFile(true) : await applyUpdate(true);
      for (;;) {
        const task = await fetchTaskDetail(task_id);
        setProgress(task.progress ?? 0);
        if (task.status === "failed") { setPhase("failed"); setError(task.error); return; }
        if (task.status === "succeeded") break;
        await wait(1500);
      }
      if (restarts) {
        // Hyperlite restarts a few seconds after its task ends; success is the new version answering. The server's
        // watchdog puts the previous one back if it does not start: that answers too, with the old version.
        setPhase("restarting");
        const deadline = Date.now() + 120000;
        let running = null;
        while (Date.now() < deadline) {
          await wait(2000);
          try {
            running = (await fetchHealth()).hyperlite_version ?? null;
            if (running === target) break;
          } catch { /* restarting */ }
        }
        if (running !== target) {
          setPhase("failed");
          setError(running ? t("upd.rolledBackNow", { version: running }) : t("upd.noAnswer"));
          return;
        }
      }
      setRestarted(restarts);
      setAfter(await fetchUpdateCheck(lang).catch(() => null));
      setPhase("ok");
    } catch (e) {
      setPhase("failed");
      setError(errorMessage(e));
    }
  }

  const step = [...STEPS].reverse().find(([pct]) => progress >= pct)?.[1] ?? "upd.step.prepare";
  const rebootNow = after?.system?.restart_required;

  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div ref={dialog} tabIndex={-1} className="nx-dialog" role="dialog" aria-modal="true" aria-labelledby="upd-title" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
        <h2 id="upd-title">{t("upd.title")}</h2>
        {error && <p className="nx-error" role="alert">{error}</p>}

        {phase === "idle" && !info && !error && !fromFile && <p className="nx-muted" role="status">{t("upd.checking")}</p>}

        {phase === "idle" && fromFile && (
          <>
            <p className="nx-muted" style={{ margin: 0 }}>{t("upd.fileHelp")}</p>
            {!file && sending === null && (
              <label className="nx-fg" style={{ display: "block", marginTop: "var(--space-3)" }}>
                <span>{t("upd.fileChoose")}</span>
                <input type="file" accept=".tar,application/x-tar" className="nx-inp" onChange={(e) => send(e.target.files?.[0])} />
              </label>
            )}
            {sending !== null && (
              <div role="status" aria-live="polite" style={{ marginTop: "var(--space-3)" }}>
                <progress max={100} value={sending} style={{ width: "100%" }} aria-label={t("upd.fileSending")} />
                <p className="nx-muted" style={{ margin: 0 }}>{sending < 100 ? t("upd.fileSending") : t("upd.fileChecking")}</p>
              </div>
            )}
            {file && (
              <>
                <dl className="nx-dl">
                  <dt>Hyperlite</dt>
                  <dd>{file.version === file.installed ? t("upd.fileSame", { version: file.version }) : t("upd.hyperliteNew", { from: file.installed, to: file.version })}</dd>
                  <dt>{t("upd.debian")}</dt>
                  <dd>{t("upd.fileDebian", { debian: file.debian })}</dd>
                </dl>
                {file.oldest && <p className="nx-error" role="alert">{t("upd.fileOlder", { version: file.version, installed: file.installed })}</p>}
                {file.notes && (
                  <section aria-label={t("upd.notes")} className="nx-logs" tabIndex={0} style={{ maxHeight: "14rem", overflow: "auto", whiteSpace: "pre-wrap" }}>
                    <strong>{file.version}</strong>{"\n"}{file.notes.trim()}
                  </section>
                )}
                <p className="nx-muted" style={{ margin: 0 }}>{t("upd.safe")}</p>
              </>
            )}
            <div className="nx-dialog-actions">
              <button type="button" className="nx-btn" onClick={onClose}>{t("upd.close")}</button>
              {file
                ? <button type="button" className="nx-btn" onClick={dropFile}>{t("upd.fileRemove")}</button>
                : <button type="button" className="nx-btn" disabled={sending !== null} onClick={() => setFromFile(false)}>{t("upd.fileBack")}</button>}
              <button type="button" className="nx-btn nx-btn--primary" disabled={!file || file.oldest} onClick={() => start("file")}>{t("upd.fileApply")}</button>
            </div>
          </>
        )}

        {phase === "idle" && !fromFile && error && !info && (
          <div className="nx-dialog-actions">
            <button type="button" className="nx-btn" onClick={onClose}>{t("upd.close")}</button>
            <button type="button" className="nx-btn" onClick={() => { setError(null); setFromFile(true); }}>{t("upd.fromFile")}</button>
          </div>
        )}

        {phase === "idle" && info && !fromFile && (
          <>
            {!info.verifiable && <p role="alert">{info.error}</p>}
            {!info.verifiable && <p className="nx-muted" style={{ margin: 0 }}>{t("upd.offlineHint")}</p>}
            {info.verifiable && (
              <>
                <dl className="nx-dl">
                  <dt>Hyperlite</dt>
                  <dd>{hyperliteNew ? t("upd.hyperliteNew", { from: info.commit_local, to: info.remote_commit }) : t("upd.hyperliteCurrent", { version: info.commit_local })}</dd>
                  <dt>{t("upd.debian")}</dt>
                  <dd>{!sys ? t("upd.debianUnknown") : debianNew ? t(sys.packages === 1 ? "upd.debianOne" : "upd.debianMany", { n: sys.packages, s: sys.security }) : t("upd.debianCurrent")}</dd>
                </dl>
                {info.rolled_back && <p className="nx-banner" role="status">{t("upd.rolledBack")}</p>}
                {info.notes?.length > 0 && (
                  <section aria-label={t("upd.notes")} className="nx-logs" tabIndex={0} style={{ maxHeight: "14rem", overflow: "auto", whiteSpace: "pre-wrap" }}>
                    {info.notes.map((n) => <div key={n.version} lang={n.language}><strong>{n.version}</strong>{"\n"}{n.text.trim()}{"\n\n"}</div>)}
                  </section>
                )}
                {sys?.restart_scheduled?.length > 0 && (
                  <p className="nx-banner" role="status">{t("upd.rebootAhead", { pkgs: sys.restart_scheduled.join(", ") })}</p>
                )}
                {sys?.restart_required && <p className="nx-banner" role="status">{t("upd.rebootPending")}</p>}
                <p className="nx-muted" style={{ margin: 0 }}>{nothing ? t("upd.nothing") : t("upd.safe")}</p>
              </>
            )}
            <div className="nx-dialog-actions">
              <button type="button" className="nx-btn" onClick={onClose}>{t("upd.close")}</button>
              <button type="button" className={info.verifiable ? "nx-btn nx-btn--ghost" : "nx-btn nx-btn--primary"} onClick={() => setFromFile(true)}>{t("upd.fromFile")}</button>
              {info.previous && <button type="button" className="nx-btn" onClick={back}>{t("upd.back", { version: info.previous })}</button>}
              {info.verifiable && <button type="button" className="nx-btn nx-btn--primary" disabled={nothing} onClick={() => start("online")}>{t("upd.apply")}</button>}
            </div>
          </>
        )}

        {busy && (
          <div role="status" aria-live="polite">
            <progress max={100} value={phase === "restarting" ? 95 : Math.max(progress, 5)} style={{ width: "100%" }} aria-label={t("upd.title")} />
            <p>{t(phase === "restarting" ? "upd.step.verify" : step)}</p>
            <p className="nx-muted" style={{ margin: 0 }}>{t("upd.keepOpen")}</p>
          </div>
        )}

        {phase === "ok" && (
          <>
            <p role="status">{t(restarted ? "upd.doneReload" : "upd.done")}</p>
            {rebootNow && <p className="nx-banner" role="status">{t("upd.rebootNow")}</p>}
            <div className="nx-dialog-actions">
              {rebootNow && <button type="button" className="nx-btn" onClick={() => { onClose(); navigateTo("node", "local", "system"); }}>{t("upd.goReboot")}</button>}
              {restarted
                ? <button type="button" className="nx-btn nx-btn--primary" onClick={() => window.location.reload()}>{t("upd.reload")}</button>
                : <button type="button" className="nx-btn nx-btn--primary" onClick={onClose}>{t("upd.close")}</button>}
            </div>
          </>
        )}

        {phase === "failed" && (
          <div className="nx-dialog-actions">
            <button type="button" className="nx-btn" onClick={onClose}>{t("upd.close")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
