import { useEffect, useRef, useState } from "react";
import { applyUpdate, fetchHealth, fetchTaskDetail, fetchUpdateCheck } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";

// One update per node (docs/design/updates-1.0.md): Hyperlite and the node's Debian packages together, with the
// release notes and, before anything starts, whether the node will need a reboot. Running VMs are not stopped.
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
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  const dialog = useRef(null);
  const busy = phase === "updating" || phase === "restarting";

  // The dialog itself takes the focus at once: Escape closes it even while the check is still running.
  useEffect(() => { dialog.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);
  useEffect(() => {
    let alive = true;
    fetchUpdateCheck(lang).then((r) => alive && setInfo(r)).catch((e) => alive && setError(errorMessage(e)));
    return () => { alive = false; };
  }, [lang]);

  const sys = info?.systeme;
  const hyperliteNew = Boolean(info?.verifiable && !info.a_jour);
  const debianNew = (sys?.paquets ?? 0) > 0;
  const nothing = info?.verifiable && !hyperliteNew && !debianNew;

  async function start() {
    setPhase("updating");
    setError(null);
    try {
      const { task_id } = await applyUpdate(true);
      for (;;) {
        const task = await fetchTaskDetail(task_id);
        setProgress(task.progres ?? 0);
        if (task.statut === "echec") { setPhase("failed"); setError(task.erreur); return; }
        if (task.statut === "termine") break;
        await wait(1500);
      }
      if (hyperliteNew) {
        // Hyperlite restarts a few seconds after its task ends; success is the new version answering. The server's
        // watchdog puts the previous one back if it does not start: that answers too, with the old version.
        setPhase("restarting");
        const deadline = Date.now() + 120000;
        let running = null;
        while (Date.now() < deadline) {
          await wait(2000);
          try {
            running = (await fetchHealth()).hyperlite_version ?? null;
            if (running === info.commit_distant) break;
          } catch { /* restarting */ }
        }
        if (running !== info.commit_distant) {
          setPhase("failed");
          setError(running ? t("upd.rolledBackNow", { version: running }) : t("upd.noAnswer"));
          return;
        }
      }
      setAfter(await fetchUpdateCheck(lang).catch(() => null));
      setPhase("ok");
    } catch (e) {
      setPhase("failed");
      setError(errorMessage(e));
    }
  }

  const step = [...STEPS].reverse().find(([pct]) => progress >= pct)?.[1] ?? "upd.step.prepare";
  const rebootNow = after?.systeme?.redemarrage_requis;

  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div ref={dialog} tabIndex={-1} className="nx-dialog" role="dialog" aria-modal="true" aria-labelledby="upd-title" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
        <h2 id="upd-title">{t("upd.title")}</h2>
        {error && <p className="nx-error" role="alert">{error}</p>}

        {phase === "idle" && !info && !error && <p className="nx-muted" role="status">{t("upd.checking")}</p>}

        {phase === "idle" && info && (
          <>
            {!info.verifiable && <p role="alert">{info.erreur}</p>}
            {info.verifiable && (
              <>
                <dl className="nx-dl">
                  <dt>Hyperlite</dt>
                  <dd>{hyperliteNew ? t("upd.hyperliteNew", { from: info.commit_local, to: info.commit_distant }) : t("upd.hyperliteCurrent", { version: info.commit_local })}</dd>
                  <dt>{t("upd.debian")}</dt>
                  <dd>{!sys ? t("upd.debianUnknown") : debianNew ? t(sys.paquets === 1 ? "upd.debianOne" : "upd.debianMany", { n: sys.paquets, s: sys.securite }) : t("upd.debianCurrent")}</dd>
                </dl>
                {info.rolled_back && <p className="nx-banner" role="status">{t("upd.rolledBack")}</p>}
                {info.notes?.length > 0 && (
                  <section aria-label={t("upd.notes")} className="nx-logs" tabIndex={0} style={{ maxHeight: "14rem", overflow: "auto", whiteSpace: "pre-wrap" }}>
                    {info.notes.map((n) => <div key={n.version} lang={n.langue}><strong>{n.version}</strong>{"\n"}{n.texte.trim()}{"\n\n"}</div>)}
                  </section>
                )}
                {sys?.redemarrage_prevu?.length > 0 && (
                  <p className="nx-banner" role="status">{t("upd.rebootAhead", { pkgs: sys.redemarrage_prevu.join(", ") })}</p>
                )}
                {sys?.redemarrage_requis && <p className="nx-banner" role="status">{t("upd.rebootPending")}</p>}
                <p className="nx-muted" style={{ margin: 0 }}>{nothing ? t("upd.nothing") : t("upd.safe")}</p>
              </>
            )}
            <div className="nx-dialog-actions">
              <button type="button" className="nx-btn" onClick={onClose}>{t("upd.close")}</button>
              <button type="button" className="nx-btn nx-btn--primary" disabled={!info.verifiable || nothing} onClick={start}>{t("upd.apply")}</button>
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
            <p role="status">{t(hyperliteNew ? "upd.doneReload" : "upd.done")}</p>
            {rebootNow && <p className="nx-banner" role="status">{t("upd.rebootNow")}</p>}
            <div className="nx-dialog-actions">
              {rebootNow && <button type="button" className="nx-btn" onClick={() => { onClose(); navigateTo("node", "local", "system"); }}>{t("upd.goReboot")}</button>}
              {hyperliteNew
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
