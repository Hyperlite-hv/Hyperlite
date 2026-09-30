import { useCallback, useEffect, useState } from "react";
import { PencilLine, Play, Square, SquareTerminal, TerminalSquare, Trash2 } from "lucide-react";
import {
  fetchContainer, updateContainer, startContainer, stopContainer, deleteContainer,
  fetchContainerBackups, createContainerBackup, deleteContainerBackup, restoreContainerBackup,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { promptText } from "../../store/usePromptStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import { refreshExtras } from "../lib/inventory";
import { NAME_RE } from "../lib/containerImages";
import StatusIndicator from "../components/StatusIndicator";
import NotesCard from "../components/NotesCard";
import { ErrorState } from "../components/States";
import { Card, Field, Loading, TableWrap } from "../components/ui";
import { ContainerLogs } from "./ContainersPage";
import { openShell, renameContainerFlow } from "../lib/containerActions";

const intIn = (v, min, max) => v !== "" && Number.isInteger(Number(v)) && Number(v) >= min && (max == null || Number(v) <= max);
const openTerminal = (name) => window.open(`/container-terminal/${encodeURIComponent(name)}`, `hyperlite-ct-terminal-${name}`, "width=1000,height=700,noopener");
function useContainerDetail(name) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { setDetail(await fetchContainer(name)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [name]);
  useEffect(() => { setDetail(null); load(); }, [load]);
  return { detail, error, load, setDetail };
}

// Start, stop, terminal, shell, rename and delete in the container's header: the same rules and confirmations as the list.
export function ContainerHeaderActions({ ct }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const on = ct.etat === "actif";
  const [busy, setBusy] = useState(false);
  if (!caps.admin) return null;
  async function run(fn, ok, confirm) {
    if (confirm && !(await confirmAction(confirm))) return false;
    setBusy(true);
    try { await fn(ct.nom); pushToast({ kind: "success", title: ok, message: ct.nom }); await refreshExtras(); return true; }
    catch (e) { pushToast({ kind: "error", title: t("action.failed", { action: ok }), message: errorMessage(e) }); return false; }
    finally { setBusy(false); }
  }
  return (
    <div className="nx-oh-acts">
      {on ? (
        <>
          {ct.mode !== "application" && <button type="button" className="nx-btn nx-btn--primary" onClick={() => openTerminal(ct.nom)}><SquareTerminal size={15} aria-hidden="true" />{t("ct.terminal")}</button>}
          <button type="button" className={`nx-btn${ct.mode === "application" ? " nx-btn--primary" : ""}`} title={t("ct.shellHelp")} onClick={() => openShell(ct.nom)}><TerminalSquare size={15} aria-hidden="true" />{t("ct.shell")}</button>
          <button type="button" className="nx-btn" disabled={busy} onClick={() => run(stopContainer, t("ct.stopRequested"), { title: t("ct.stopTitle", { name: ct.nom }), message: t("ct.stopMsg"), confirmLabel: t("ct.stop") })}><Square size={15} aria-hidden="true" />{t("ct.stop")}</button>
        </>
      ) : <>
        <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={() => run(startContainer, t("ct.started"))}><Play size={15} aria-hidden="true" />{t("ct.start")}</button>
        <button type="button" className="nx-btn" disabled={busy} onClick={async () => {
          const name = await renameContainerFlow(ct, t, pushToast);
          if (name) { await refreshExtras(); navigateTo("container", name, "summary"); }
        }}><PencilLine size={15} aria-hidden="true" />{t("ct.rename")}</button>
      </>}
      <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("a11y.delete_container_x", { v: ct.nom })} disabled={busy}
        onClick={async () => {
          const done = await run(deleteContainer, t("ct.deleted"), { title: t("ct.deleteTitle", { name: ct.nom }), message: t("ct.deleteMsg", { name: ct.nom }), confirmLabel: t("menu.delete").replace("…", ""), danger: true });
          if (done) navigateTo("datacenter", null, "containers");
        }}><Trash2 size={15} aria-hidden="true" /></button>
    </div>
  );
}

// Resources (memory live, CPUs at the next start), network, DNS servers, start at boot, notes.
export function ContainerSummary({ resource: ct }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const { detail, error, load, setDetail } = useContainerDetail(ct.nom);
  const [vcpu, setVcpu] = useState("");
  const [mem, setMem] = useState("");
  const [dns, setDns] = useState("");
  const [boot, setBoot] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!detail) return;
    setVcpu(String(detail.vcpu)); setMem(String(detail.memoire_mo)); setDns((detail.dns || []).join(", ")); setBoot(detail.demarrage_auto);
  }, [detail]);
  if (error && !detail) return <ErrorState message={error} onRetry={load} />;
  if (!detail) return <Loading />;

  const bad = { vcpu: !intIn(vcpu, 1, 4096), mem: !intIn(mem, 128) };
  const resDirty = Number(vcpu) !== detail.vcpu || Number(mem) !== detail.memoire_mo;
  const dnsList = dns.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
  const dnsDirty = detail.dns != null && dnsList.join(",") !== (detail.dns || []).join(",");

  async function save(payload, okTitle) {
    setBusy(true);
    try {
      const r = await updateContainer(ct.nom, payload);
      setDetail(r);
      pushToast({ kind: "success", title: okTitle, message: r.a_redemarrer ? t("cd.nextStart") : ct.nom });
      refreshExtras();
    } catch (e) {
      setBoot(detail.demarrage_auto); // the box shows what the server kept
      pushToast({ kind: "error", title: t("cd.saveFailed"), message: errorMessage(e) });
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="nx-cols2">
        <Card title={t("cd.resources")}>
          <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("cd.resourcesHelp")}</p>
          <div className="nx-fg nx-fg--2">
            <Field label="vCPU" error={bad.vcpu ? t("cd.vcpuRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={1} disabled={!caps.admin} value={vcpu} onChange={(e) => setVcpu(e.target.value)} />}</Field>
            <Field label={t("ct.memory")} unit={lang === "fr" ? "Mo" : "MB"} error={bad.mem ? t("cd.memRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={128} disabled={!caps.admin} value={mem} onChange={(e) => setMem(e.target.value)} />}</Field>
          </div>
          {caps.admin && <div className="nx-fa"><button type="button" className="nx-btn" disabled={!resDirty || busy || bad.vcpu || bad.mem} onClick={() => save({ vcpu: Number(vcpu), memory_mb: Number(mem) }, t("cd.resourcesSaved"))}>{t("cd.save")}</button></div>}
        </Card>
        <Card title={t("cd.network")}>
          <dl className="nx-dl2">
            <dt>IP</dt><dd className="nx-mono">{detail.ip || <span className="nx-muted">{t("ct.noIp")}</span>}</dd>
            <dt>{t("cd.interfaces")}</dt>
            <dd>{detail.interfaces.length === 0 ? <span className="nx-muted">—</span> : <ul className="nx-plainlist">{detail.interfaces.map((i) => <li key={i.mac || i.reseau} className="nx-mono">{i.reseau || "—"} · {i.mac || "—"}</li>)}</ul>}</dd>
            {detail.utilisateur_ssh && <><dt>SSH</dt><dd className="nx-mono">{detail.utilisateur_ssh}{detail.ip ? `@${detail.ip}` : ""}</dd></>}
          </dl>
          {detail.dns == null ? <p className="nx-muted" style={{ margin: "var(--space-3) 0 0" }}>{t("cd.dnsUnknown")}</p> : (
            <>
              <Field label={t("cd.dns")} hint={t("cd.dnsHelp")}>{(p) => <input {...p} className="nx-inp nx-mono" disabled={!caps.admin} value={dns} onChange={(e) => setDns(e.target.value)} placeholder="1.1.1.1, 9.9.9.9" />}</Field>
              {caps.admin && <div className="nx-fa"><button type="button" className="nx-btn" disabled={!dnsDirty || busy || dnsList.length === 0} onClick={() => save({ dns: dnsList }, t("cd.dnsSaved"))}>{t("cd.save")}</button></div>}
            </>
          )}
        </Card>
      </div>
      <Card title={t("cd.options")}>
        <label className="nx-check"><input type="checkbox" checked={boot} disabled={!caps.admin || busy} onChange={(e) => { setBoot(e.target.checked); save({ demarrage_auto: e.target.checked }, t("cd.bootSaved")); }} /> {t("cd.boot")}</label>
        <p className="nx-muted" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-13)" }}>{t("cd.bootHelp")}</p>
      </Card>
      <NotesCard kind="container" name={ct.nom} canEdit={caps.admin} />
    </>
  );
}

// Terminal (system containers) or the process output (application containers).
export function ContainerConsolePage({ resource: ct }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const [logs, setLogs] = useState(false);
  const on = ct.etat === "actif";
  return (
    <Card title={t("tab.console")}>
      {ct.mode === "application" ? (
        <>
          <p className="nx-muted" style={{ marginTop: 0 }}>{t("cd.appConsole")}</p>
          <div className="nx-ra" style={{ justifyContent: "flex-start" }}>
            <button type="button" className="nx-btn" onClick={() => setLogs(true)}>{t("ct.logs")}</button>
            {on && caps.admin && <button type="button" className="nx-btn" onClick={() => openShell(ct.nom)}><TerminalSquare size={15} aria-hidden="true" />{t("ct.shell")}</button>}
          </div>
          {on && caps.admin && <p className="nx-muted" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-13)" }}>{t("ct.shellHelp")}</p>}
          <ContainerLogs name={logs ? ct.nom : null} onClose={() => setLogs(false)} />
        </>
      ) : !on ? <p className="nx-muted" style={{ margin: 0 }}>{t("cd.consoleStopped")}</p>
        : !caps.admin ? <p className="nx-muted" style={{ margin: 0 }}>{t("cd.consoleAdmin")}</p> : (
          <>
            <p className="nx-muted" style={{ marginTop: 0 }}>{t("cd.consoleHelp")}</p>
            <div className="nx-ra" style={{ justifyContent: "flex-start" }}>
              <button type="button" className="nx-btn nx-btn--primary" onClick={() => openTerminal(ct.nom)}><SquareTerminal size={15} aria-hidden="true" />{t("ct.terminal")}</button>
              <button type="button" className="nx-btn" onClick={() => openShell(ct.nom)}><TerminalSquare size={15} aria-hidden="true" />{t("ct.shell")}</button>
            </div>
            <p className="nx-muted" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-13)" }}>{t("ct.shellHelp")}</p>
          </>
        )}
    </Card>
  );
}

const backupWire = (s) => (s === "termine" ? "termine" : s === "echec" ? "echec" : "en_cours");

// This container's backups: create (stopped container), restore to a new container, delete.
export function ContainerBackupsPage({ resource: ct }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const [backups, setBackups] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { const b = await fetchContainerBackups(); setBackups((Array.isArray(b) ? b : []).filter((x) => x.container_name === ct.nom)); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, [ct.nom]);
  useEffect(() => { if (caps.admin) load(); }, [load, caps.admin]);
  if (!caps.admin) return <Card title={t("tab.backups")}><p className="nx-muted" style={{ margin: 0 }}>{t("cd.backupsAdmin")}</p></Card>;
  if (error && !backups) return <ErrorState message={error} onRetry={load} />;
  const on = ct.etat === "actif";
  const del = t("menu.delete").replace("…", "");
  const nameCheck = (v) => (NAME_RE.test(v) ? "" : t("ct.nameRule"));
  async function backupNow() {
    try { await createContainerBackup(ct.nom); pushToast({ kind: "success", title: t("ct.backupStarted"), message: ct.nom }); setTimeout(load, 2000); }
    catch (e) { pushToast({ kind: "error", title: t("ct.backupFailed"), message: errorMessage(e) }); }
  }
  async function restore(b) {
    const n = await promptText({ title: t("ct.restoreTitle", { name: b.container_name }), label: t("ct.restoredName"), defaultValue: `${b.container_name}-restored`, confirmLabel: t("ct.restore"), validate: nameCheck });
    if (!n || !n.trim()) return;
    try { await restoreContainerBackup(b.id, n.trim()); pushToast({ kind: "success", title: t("ct.restored"), message: n.trim() }); refreshExtras(); }
    catch (e) { pushToast({ kind: "error", title: t("ct.restoreFailed"), message: errorMessage(e) }); }
  }
  async function remove(b) {
    if (!(await confirmAction({ title: t("ct.backupDeleteTitle"), message: t("ct.backupDeleteMsg", { name: b.container_name }), confirmLabel: del, danger: true }))) return;
    try { await deleteContainerBackup(b.id); pushToast({ kind: "success", title: t("ct.backupDeleted") }); load(); }
    catch (e) { pushToast({ kind: "error", title: t("ct.deleteFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("tab.backups")} note={backups?.length || null} flush={Boolean(backups?.length)}
      actions={<button type="button" className="nx-btn nx-btn--sm" aria-disabled={on || undefined} title={on ? t("menu.reason.mustStop") : undefined} onClick={() => !on && backupNow()}>{t("ct.backup")}</button>}>
      {backups == null ? <Loading /> : backups.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("ct.backupsNone")}</p> : (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ct.date")}</th><th scope="col" className="nx-num">{t("ct.size")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {backups.map((b) => (
                <tr key={b.id}>
                  <td><StatusIndicator kind="task" wire={backupWire(b.statut)} /></td>
                  <th scope="row" style={{ fontWeight: 400 }}>{new Date(b.cree_le).toLocaleString(lang)}</th>
                  <td className="nx-num nx-mono">{b.taille_octets ? formatSizeMb(b.taille_octets / 1048576, lang) : "—"}</td>
                  <td><div className="nx-ra">
                    {b.statut === "termine" && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("a11y.restore_backup_x", { v: b.id })} onClick={() => restore(b)}>{t("ct.restore")}</button>}
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_backup_x", { v: b.id })} title={del} onClick={() => remove(b)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}
