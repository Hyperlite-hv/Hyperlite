import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, Check, CircleAlert, CircleDashed, FileUp, History, LoaderCircle, Minus, RefreshCw, RotateCcw, ShieldAlert, X } from "lucide-react";
import {
  applyUpdate, applyUpdateFile, deleteUpdateFile, fetchUpdateCheck, fetchUpdateFile, fetchUpdateHistory, fetchUpdatePreflight,
  fetchUpdateRun, fetchUpdateStatus, rollbackUpdate, saveUpdateSettings, uploadUpdateFile,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, relativeTime } from "../lib/format";
import { PageHeader, SideDrawer, TableWrap } from "../components/ui";
import { Cmd } from "../components/portal";
import { InlineError } from "../components/States";

const asList = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []);
const ms = (iso) => (iso ? Date.parse(iso) : null);

// The state of the node in one word, the most pressing first.
function stateOf(status, checking) {
  if (!status) return null;
  if (status.current) return status.current.status === "verifying" ? "restarting" : "installing";
  if (checking) return "checking";
  const last = status.last_check;
  if (last && !last.verifiable) return "error";
  if (last && !last.up_to_date && last.candidate) return last.critical ? "critical" : "available";
  if (last && (last.system?.packages || 0) > 0) return last.critical ? "critical" : "available";
  if (status.reboot_required) return "reboot";
  return last ? "uptodate" : "unknown";
}

// Release notes are short Markdown: headings and bullets, shown as such (no HTML from the network).
function Notes({ text }) {
  return (
    <div className="hc-up-notes">{text.split("\n").map((line, i) => {
      const l = line.trimEnd();
      if (l.startsWith("### ")) return <h4 key={i}>{l.slice(4)}</h4>;
      if (l.startsWith("## ")) return <h3 key={i}>{l.slice(3)}</h3>;
      if (l.startsWith("# ")) return <h3 key={i}>{l.slice(2)}</h3>;
      if (/^\s*[-*] /.test(l)) return <p key={i} className="hc-up-li" style={{ paddingLeft: `${(l.search(/\S/) / 2) * 0.8 + 0.8}rem` }}>{l.replace(/^\s*[-*] /, "").replace(/\*\*/g, "")}</p>;
      return l ? <p key={i}>{l.replace(/\*\*/g, "")}</p> : null;
    })}</div>
  );
}

const STEP_ICON = { pending: CircleDashed, running: LoaderCircle, done: Check, failed: X, skipped: Minus };

// The steps of a run: each with its status, its detail, its time, and a bar only when the step measured its progress.
export function RunSteps({ run }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  return (
    <ol className="hc-up-steps">
      {run.steps.map((s) => {
        const Icon = STEP_ICON[s.status] || CircleDashed;
        const took = s.started_at && s.finished_at ? Math.max(0, Math.round((ms(s.finished_at) - ms(s.started_at)) / 1000)) : null;
        return (
          <li key={s.id} data-status={s.status}>
            <span className="hc-up-ico" aria-hidden="true"><Icon size={14} className={s.status === "running" ? "nx-spin" : undefined} /></span>
            <div className="hc-up-st">
              <b>{t(`upd2.step.${s.id}`)}</b>
              <span className="nx-sr">{t(`upd2.sstatus.${s.status}`)}</span>
              {s.detail && <small>{s.detail}</small>}
              {s.status === "running" && (s.progress != null
                ? <span className="nx-tray-bar" role="progressbar" aria-label={t(`upd2.step.${s.id}`)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(s.progress)}><span style={{ width: `${s.progress}%` }} /></span>
                : <span className="nx-tray-bar is-indet" role="progressbar" aria-label={t(`upd2.step.${s.id}`)} aria-valuetext={t("upd2.unknownLength")}><span /></span>)}
            </div>
            <span className="hc-up-time nx-muted">
              {s.status === "running" && s.progress != null ? `${Math.round(s.progress)} %` : took != null ? t("upd2.secs", { n: took }) : s.started_at && s.status === "running" ? formatDateTime(s.started_at, lang).slice(-8) : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function RunDrawer({ runId, onClose }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!runId) return;
    setRun(null); setError(null);
    fetchUpdateRun(runId).then(setRun).catch((e) => setError(errorMessage(e)));
  }, [runId]);
  return (
    <SideDrawer open={!!runId} title={run ? t("upd2.runTitle", { from: run.from_version || "?", to: run.to_version || "?" }) : t("upd2.run")} onClose={onClose}>
      {error && <InlineError message={error} />}
      {!run && !error && <p className="nx-muted">{t("loading")}</p>}
      {run && (
        <>
          <p className="hc-up-result" data-status={run.status}>{t(`upd2.rstatus.${run.status}`)}{run.duration_s != null && <span className="nx-muted"> · {t("upd2.secs", { n: run.duration_s })}</span>}</p>
          <p className="nx-muted" style={{ margin: 0 }}>{t(`upd2.kind.${run.kind}`)} · {run.started_by || "—"} · {formatDateTime(run.started_at, lang)}</p>
          {run.error && <p className="nx-error" role="alert">{run.error}</p>}
          <h3 className="hc-al-h">{t("upd2.steps")}</h3>
          <RunSteps run={run} />
          <details className="hc-up-log">
            <summary>{t("upd2.techLog", { n: run.log.length })}</summary>
            <pre className="hc-run-pre" tabIndex={0}>{run.log.join("\n") || t("upd2.noLog")}</pre>
          </details>
          {run.watchdog_log?.length > 0 && (
            <details className="hc-up-log">
              <summary>{t("upd2.watchdogLog")}</summary>
              <pre className="hc-run-pre" tabIndex={0}>{run.watchdog_log.join("\n")}</pre>
            </details>
          )}
        </>
      )}
    </SideDrawer>
  );
}

// From a file, for a node without network: the file published next to the ISO, checked by the node.
function FileUpdate({ onStarted, disabled }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [file, setFile] = useState(null);
  const [sending, setSending] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { fetchUpdateFile(lang).then((f) => setFile(f?.version ? f : null)).catch(() => {}); }, [lang]);
  async function send(chosen) {
    if (!chosen) return;
    setError(null); setFile(null); setSending(0);
    try { setFile(await uploadUpdateFile(chosen, lang, setSending)); } catch (e) { setError(errorMessage(e)); } finally { setSending(null); }
  }
  async function apply() {
    setError(null);
    try { await applyUpdateFile(true); onStarted(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <div className="hc-up-file">
      <p className="nx-muted">{t("upd.fileHelp")}</p>
      {error && <InlineError message={error} />}
      {!file && sending === null && <label className="hc-mx-f"><span>{t("upd.fileChoose")}</span><input type="file" accept=".tar,application/x-tar" className="nx-inp" onChange={(e) => send(e.target.files?.[0])} /></label>}
      {sending !== null && <div role="status"><span className="nx-tray-bar" role="progressbar" aria-label={t("upd.fileSending")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={sending}><span style={{ width: `${sending}%` }} /></span><small className="nx-muted">{sending < 100 ? t("upd.fileSending") : t("upd.fileChecking")}</small></div>}
      {file && (
        <>
          <p><b>{file.version === file.installed ? t("upd.fileSame", { version: file.version }) : t("upd.hyperliteNew", { from: file.installed, to: file.version })}</b> · {t("upd.fileDebian", { debian: file.debian })}</p>
          {file.oldest && <p className="nx-error" role="alert">{t("upd.fileOlder", { version: file.version, installed: file.installed })}</p>}
          <div className="nx-inline">
            <button type="button" className="nx-btn" onClick={() => deleteUpdateFile().then(() => setFile(null)).catch((e) => setError(errorMessage(e)))}>{t("upd.fileRemove")}</button>
            <button type="button" className="nx-btn nx-btn--primary" disabled={file.oldest || disabled} onClick={apply}>{t("upd.fileApply")}</button>
          </div>
        </>
      )}
    </div>
  );
}

// Updates: the node's version and what is available, the checks, the update in progress followed step by step from the
// server (a reload or another browser sees the same), the history and the settings. The update itself runs on the
// node, out of the service: closing this page changes nothing.
export default function UpdatesPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [status, setStatus] = useState(null);
  const [unreachable, setUnreachable] = useState(false);
  const [history, setHistory] = useState(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState(null);
  const [preflight, setPreflight] = useState(null);
  const [refusal, setRefusal] = useState(null);
  const [system, setSystem] = useState(true);
  const [acknowledged, setAcknowledged] = useState(false);
  const [starting, setStarting] = useState(false);
  const [openRun, setOpenRun] = useState(null);
  const [fromFile, setFromFile] = useState(false);
  const lastCurrent = useRef(null);

  const load = useCallback(async () => {
    try {
      const s = await fetchUpdateStatus();
      setStatus(s); setUnreachable(false);
      // A run that just ended: say it once, and show the history with it.
      if (lastCurrent.current && !s.current) {
        const done = s.last_run;
        if (done && done.id === lastCurrent.current) {
          const kind = done.status === "succeeded" ? "success" : "error";
          pushToast({ kind, title: t(`upd2.rstatus.${done.status}`), message: done.to_version || "" });
          // Hyperlite itself changed: this page is the old version's code, the new one is loaded.
          if (done.status !== "failed" && done.from_version && done.to_version && done.from_version !== done.to_version) setTimeout(() => window.location.reload(), 2500);
        }
        fetchUpdateHistory().then((r) => setHistory(asList(r))).catch(() => {});
      }
      lastCurrent.current = s.current?.id ?? null;
    } catch {
      setUnreachable(true); // the service restarts during an update: try again shortly
    }
  }, [pushToast, t]);
  useEffect(() => { load(); fetchUpdateHistory().then((r) => setHistory(asList(r))).catch(() => setHistory([])); }, [load]);
  const active = Boolean(status?.current) || unreachable;
  useEffect(() => {
    const timer = setInterval(load, active ? 2000 : 30000);
    return () => clearInterval(timer);
  }, [active, load]);
  const state = stateOf(status, checking);
  const last = status?.last_check;
  const hyperliteNew = Boolean(last?.verifiable && !last.up_to_date && last.candidate);
  const debianNew = (last?.system?.packages || 0) > 0;
  const something = hyperliteNew || debianNew;
  useEffect(() => { if (something && !status?.current) fetchUpdatePreflight().then(setPreflight).catch(() => setPreflight(null)); }, [something, status?.current]);
  const warnings = (preflight?.checks || []).filter((c) => c.status === "warning");
  const blocked = (preflight?.checks || []).some((c) => c.status === "blocking");

  async function check() {
    setChecking(true); setCheckError(null);
    try {
      const r = await fetchUpdateCheck(lang);
      if (!r.verifiable) setCheckError(r.error);
      else pushToast({ kind: "info", title: t("upd2.checked"), message: r.up_to_date && !(r.system?.packages > 0) ? t("upd2.state.uptodate") : t("upd2.state.available") });
      await load();
    } catch (e) { setCheckError(errorMessage(e)); } finally { setChecking(false); }
  }
  async function start() {
    setStarting(true); setRefusal(null);
    try {
      await applyUpdate(system);
      setAcknowledged(false);
      await load();
    } catch (e) {
      setRefusal({ message: errorMessage(e), checks: e.detail?.checks || null });
    } finally { setStarting(false); }
  }
  async function back() {
    if (!(await confirmAction({ title: t("upd.backTitle", { version: status.previous }), message: t("upd.backMsg"), confirmLabel: t("upd.back", { version: status.previous }) }))) return;
    try { await rollbackUpdate(); await load(); } catch (e) { pushToast({ kind: "error", title: t("upd2.failedStart"), message: errorMessage(e) }); }
  }
  async function setInterval_(hours) {
    try { const r = await saveUpdateSettings({ check_interval_h: hours }); setStatus((s) => ({ ...s, check_interval_h: r.check_interval_h })); }
    catch (e) { pushToast({ kind: "error", title: t("upd2.settingFailed"), message: errorMessage(e) }); }
  }

  if (!status && !unreachable) return <><PageHeader title={t("tab.updates")} /><p className="nx-muted">{t("loading")}</p></>;
  const current = status?.current;

  return (
    <>
      <PageHeader title={t("tab.updates")} desc={t("upd2.desc")} actions={(
        <>
          <Cmd icon={RefreshCw} label={checking ? t("upd2.checking") : t("upd2.check")} onClick={check} disabled={checking || !!current} />
          {status?.previous && <Cmd icon={RotateCcw} label={t("upd.back", { version: status.previous })} onClick={back} disabled={!!current} />}
          <Cmd icon={FileUp} label={t("upd2.fromFile")} onClick={() => setFromFile(!fromFile)} aria-pressed={fromFile} disabled={!!current} />
        </>
      )} />

      {unreachable && <div className="nx-bn" data-tone="info" role="status"><LoaderCircle size={16} className="nx-spin" aria-hidden="true" /><span className="nx-bn-t">{t("upd2.restartingService")}</span></div>}
      {status?.reboot_required && !current && <div className="nx-bn" data-tone="warning" role="status"><CircleAlert size={16} aria-hidden="true" /><span className="nx-bn-t">{t("upd2.rebootNeeded")}</span></div>}

      {status && (
        <div className="hc-up-cards">
          <div className="hc-up-card hc-up-card--state" data-state={state}>
            <small>{t("upd2.status")}</small>
            <b>{t(`upd2.state.${state}`)}</b>
            {state === "critical" && <span className="hc-up-crit"><ShieldAlert size={14} aria-hidden="true" />{t("upd2.securityHint")}</span>}
          </div>
          <div className="hc-up-card"><small>{t("upd2.installed")}</small><b className="nx-mono">{status.installed || "—"}</b></div>
          <div className="hc-up-card"><small>{t("upd2.latest")}</small><b className="nx-mono">{last?.verifiable ? (last.candidate || status.installed) : "—"}</b>{status.channel && <span className="nx-muted">{t("upd2.channel", { channel: status.channel })}</span>}</div>
          <div className="hc-up-card"><small>{t("upd2.lastCheck")}</small><b>{last?.at ? relativeTime(ms(last.at), lang) : t("upd2.never")}</b>
            <label className="hc-up-int"><span className="nx-sr">{t("upd2.interval")}</span>
              <select className="nx-inp nx-inp--sm" value={status.check_interval_h} onChange={(e) => setInterval_(Number(e.target.value))} aria-label={t("upd2.interval")}>
                {[0, 1, 6, 24].map((h) => <option key={h} value={h}>{h ? t("upd2.every", { n: h }) : t("upd2.manual")}</option>)}
              </select></label>
          </div>
        </div>
      )}
      {checkError && <InlineError message={t("upd2.checkFailed", { error: checkError })} />}
      {last && !last.verifiable && !checkError && <InlineError message={t("upd2.checkFailed", { error: last.error })} />}

      {current && (
        <section className="hc-up-sec" aria-labelledby="up-cur">
          <h2 id="up-cur">{current.kind === "rollback" ? t("upd2.rollingBack", { to: current.to_version }) : t("upd2.inProgress", { from: current.from_version || "?", to: current.to_version || "?" })}</h2>
          <p className="nx-muted">{current.status === "verifying" ? t("upd2.verifyingHelp") : t("upd2.runningHelp")}</p>
          <RunSteps run={current} />
          <button type="button" className="nx-lnk" onClick={() => setOpenRun(current.id)}>{t("upd2.openLog")}</button>
        </section>
      )}

      {!current && fromFile && <section className="hc-up-sec"><h2>{t("upd2.fromFile")}</h2><FileUpdate disabled={!!current} onStarted={() => { setFromFile(false); load(); }} /></section>}

      {!current && something && !fromFile && (
        <section className="hc-up-sec" aria-labelledby="up-new">
          <h2 id="up-new">{hyperliteNew ? t("upd2.newVersion", { version: last.candidate }) : t("upd2.debianOnly")}</h2>
          <div className="hc-up-what">
            {hyperliteNew && <p>{t("upd.hyperliteNew", { from: last.installed, to: last.candidate })}</p>}
            {debianNew && <p>{t("upd2.debian", { n: last.system.packages, s: last.system.security })}{last.system.restart_scheduled?.length > 0 && <> · <b>{t("upd2.rebootAfter")}</b></>}</p>}
            {hyperliteNew && <p className="nx-muted">{t("upd2.serviceRestart")}</p>}
          </div>
          {last.notes?.length > 0 && (
            <details className="hc-up-log" open>
              <summary>{t("upd2.notes", { n: last.notes.length })}</summary>
              {last.notes.map((n) => <Fragment key={n.version}><Notes text={n.text} /></Fragment>)}
            </details>
          )}
          <h3 className="hc-al-h">{t("upd2.checks")}</h3>
          {!preflight ? <p className="nx-muted">{t("loading")}</p> : (
            <ul className="hc-up-checks">{preflight.checks.map((c) => (
              <li key={c.id} data-status={c.status}><span aria-hidden="true">{c.status === "ok" ? <Check size={14} /> : c.status === "warning" ? <CircleAlert size={14} /> : <X size={14} />}</span>
                <span><b>{t(`upd2.check.${c.id}`)}</b>{c.status !== "ok" && <small>{t(`upd2.check.${c.id}.${c.status}`)}{c.detail ? ` — ${c.detail}` : ""}</small>}</span>
                <span className="nx-sr">{t(`upd2.cstatus.${c.status}`)}</span></li>
            ))}</ul>
          )}
          {refusal && <InlineError message={refusal.message} />}
          <div className="hc-up-go">
            {hyperliteNew && <label className="nx-check"><input type="checkbox" checked={system} onChange={(e) => setSystem(e.target.checked)} />{t("upd2.withDebian")}</label>}
            {warnings.length > 0 && <label className="nx-check"><input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />{t("upd2.ackWarnings", { n: warnings.length })}</label>}
            <button type="button" className="nx-btn nx-btn--primary" disabled={starting || blocked || (warnings.length > 0 && !acknowledged)} onClick={start}>
              <ArrowDownToLine size={16} aria-hidden="true" />{starting ? t("upd2.starting") : hyperliteNew ? t("upd2.install", { version: last.candidate }) : t("upd2.installDebian")}
            </button>
          </div>
          <p className="nx-hint">{t("upd2.safety")}</p>
        </section>
      )}

      <section className="hc-up-sec" aria-labelledby="up-hist">
        <h2 id="up-hist"><History size={16} aria-hidden="true" />{t("upd2.history")}</h2>
        {history == null ? <p className="nx-muted">{t("loading")}</p> : history.length === 0 ? <p className="nx-muted">{t("upd2.noHistory")}</p> : (
          <TableWrap>
            <table className="nx-table hc-grid--list">
              <thead><tr><th scope="col">{t("upd2.when")}</th><th scope="col">{t("upd2.versions")}</th><th scope="col">{t("upd2.kindCol")}</th><th scope="col">{t("upd2.by")}</th><th scope="col">{t("upd2.duration")}</th><th scope="col">{t("upd2.result")}</th></tr></thead>
              <tbody>{history.map((r) => (
                <tr key={r.id}>
                  <th scope="row"><button type="button" className="nx-lnk" onClick={() => setOpenRun(r.id)}>{formatDateTime(r.started_at, lang)}</button></th>
                  <td className="nx-mono">{r.from_version || "?"} → {r.to_version || "?"}</td>
                  <td>{t(`upd2.kind.${r.kind}`)}{r.system ? ` · ${t("upd2.plusDebian")}` : ""}</td>
                  <td>{r.started_by || "—"}</td>
                  <td>{r.duration_s != null ? t("upd2.secs", { n: r.duration_s }) : "—"}</td>
                  <td><span className="hc-up-result" data-status={r.status}>{t(`upd2.rstatus.${r.status}`)}</span></td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </section>
      <RunDrawer runId={openRun} onClose={() => setOpenRun(null)} />
    </>
  );
}
UpdatesPage.ownHeader = true;
