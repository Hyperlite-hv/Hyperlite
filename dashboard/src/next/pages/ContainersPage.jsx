import { useCallback, useEffect, useState } from "react";
import {
  fetchContainers, startContainer, stopContainer, deleteContainer,
  cloneContainer, fetchContainerBackups, createContainerBackup, deleteContainerBackup, restoreContainerBackup,
  fetchContainerLogs,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { promptText } from "../../store/usePromptStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { ActionsContextMenu, useContextTarget } from "../components/ContextMenu";
import { ErrorState } from "../components/States";
import { NAME_RE } from "../lib/containerImages";
import { PageHeader, Card, Empty, Loading, TableWrap, Chip, SideDrawer } from "../components/ui";
import { Box, Plus, Trash2 } from "lucide-react";

const backupWire = (s) => (s === "termine" ? "termine" : s === "echec" ? "echec" : "en_cours");

// LXC containers: list with lifecycle actions, creation (image gallery + Docker Hub search), clone,
// backups and restore. Same endpoints and safeguards as the historical tab.
export default function ContainersPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const [containers, setContainers] = useState(null);
  const [error, setError] = useState(null);
  const [backups, setBackups] = useState(null);

  const reload = useCallback(async () => {
    try { const r = await fetchContainers(); setContainers(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  const reloadBackups = useCallback(() => { fetchContainerBackups().then((b) => setBackups(Array.isArray(b) ? b : [])).catch(() => setBackups([])); }, []);
  useEffect(() => { reload(); if (caps.admin) reloadBackups(); }, [reload, reloadBackups, caps.admin]);
  useEffect(() => {
    if (!(containers || []).length) return undefined;
    const id = setInterval(reload, 6000);
    return () => clearInterval(id);
  }, [containers, reload]);
  // The creation dialog (Create ▸ Container, or the button below) announces a new container.
  useEffect(() => { window.addEventListener("nx:containers-changed", reload); return () => window.removeEventListener("nx:containers-changed", reload); }, [reload]);

  const ctx = useContextTarget(); // right click on a container: the same actions as its row buttons
  const fail = (title) => (e) => pushToast({ kind: "error", title, message: errorMessage(e) });
  const nameCheck = (v) => (NAME_RE.test(v) ? "" : t("ct.nameRule"));

  async function act(fn, ct, ok) {
    try { await fn(ct.nom); pushToast({ kind: "success", title: ok, message: ct.nom }); await reload(); }
    catch (err) { fail(t("action.failed", { action: ok }))(err); }
  }
  async function stop(ct) {
    if (!(await confirmAction({ title: t("ct.stopTitle", { name: ct.nom }), message: t("ct.stopMsg"), confirmLabel: t("ct.stop") }))) return;
    act(stopContainer, ct, t("ct.stopRequested"));
  }
  async function remove(ct) {
    if (!(await confirmAction({ title: t("ct.deleteTitle", { name: ct.nom }), message: t("ct.deleteMsg", { name: ct.nom }), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    try { await deleteContainer(ct.nom); pushToast({ kind: "success", title: t("ct.deleted"), message: ct.nom }); await reload(); }
    catch (err) { fail(t("ct.deleteFailed"))(err); }
  }
  async function clone(ct) {
    const n = await promptText({ title: t("ct.cloneTitle", { name: ct.nom }), label: t("ct.copyName"), defaultValue: `${ct.nom}-clone`, confirmLabel: t("ct.clone"), validate: nameCheck });
    if (!n || !n.trim()) return;
    try { await cloneContainer(ct.nom, n.trim()); pushToast({ kind: "success", title: t("ct.cloned"), message: `${ct.nom} → ${n.trim()}` }); await reload(); }
    catch (err) { fail(t("ct.cloneFailed"))(err); }
  }
  async function backup(ct) {
    try { await createContainerBackup(ct.nom); pushToast({ kind: "success", title: t("ct.backupStarted"), message: ct.nom }); setTimeout(reloadBackups, 2000); }
    catch (err) { fail(t("ct.backupFailed"))(err); }
  }
  async function restore(b) {
    const n = await promptText({ title: t("ct.restoreTitle", { name: b.container_name }), label: t("ct.restoredName"), defaultValue: `${b.container_name}-restored`, confirmLabel: t("ct.restore"), validate: nameCheck });
    if (!n || !n.trim()) return;
    try { await restoreContainerBackup(b.id, n.trim()); pushToast({ kind: "success", title: t("ct.restored"), message: n.trim() }); await reload(); }
    catch (err) { fail(t("ct.restoreFailed"))(err); }
  }
  async function removeBackup(b) {
    if (!(await confirmAction({ title: t("ct.backupDeleteTitle"), message: t("ct.backupDeleteMsg", { name: b.container_name }), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    try { await deleteContainerBackup(b.id); pushToast({ kind: "success", title: t("ct.backupDeleted") }); await reloadBackups(); }
    catch (err) { fail(t("ct.deleteFailed"))(err); }
  }
  const [logsOf, setLogsOf] = useState(null);
  const openTerminal = (ct) => window.open(`/container-terminal/${encodeURIComponent(ct.nom)}`, `hyperlite-ct-terminal-${ct.nom}`, "width=1000,height=700,noopener");

  const list = containers || [];
  const del = t("menu.delete").replace("…", "");
  const openWizard = () => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "container" }));

  return (
    <>
      <PageHeader title={t("tab.containers")} count={containers ? list.length : null} help={t("ct.intro")}
        actions={caps.admin && <button type="button" className="nx-btn nx-btn--primary" onClick={openWizard}><Plus size={15} aria-hidden="true" />{t("ct.create")}</button>} />
      {error && containers == null ? <ErrorState message={error} onRetry={reload} /> : (
        <div className="nx-card2 nx-card2--flush">
          {containers == null ? <Loading style={{ padding: "var(--space-4)" }} /> : list.length === 0 ? (
            <Empty icon={Box} title={t("ct.none")} text={caps.admin ? t("ct.noneHelp") : t("ct.noneObserver")} action={caps.admin && <button type="button" className="nx-btn" onClick={openWizard}><Plus size={15} aria-hidden="true" />{t("ct.create")}</button>} />
          ) : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ct.name")}</th><th scope="col">{t("ct.type")}</th><th scope="col" className="nx-num">vCPU</th><th scope="col" className="nx-num">{t("ct.memory")}</th><th scope="col">IP</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {list.map((ct) => {
                    const on = ct.etat === "actif";
                    return (
                      <tr key={ct.nom} className={ctx.is("ct", ct.nom) ? "is-ctx" : undefined} onContextMenu={ctx.open("ct", ct)}>
                        <td><StatusIndicator kind="vm" wire={on ? "actif" : "arrete"} /></td>
                        <th scope="row" className="nx-mono">{ct.nom}</th>
                        <td>{ct.mode === "application"
                          ? <><Chip tone="info">Docker</Chip> <span className="nx-mono nx-muted">{ct.image}</span></>
                          : <Chip>LXC</Chip>}</td>
                        <td className="nx-num nx-mono">{ct.vcpu}</td>
                        <td className="nx-num nx-mono">{formatSizeMb(ct.memoire_mo, lang)}</td>
                        <td className="nx-mono">{ct.ip || <span className="nx-muted">{t("ct.noIp")}</span>}</td>
                        <td><div className="nx-ra">
                          {ct.mode === "application" && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("ct.logsX", { name: ct.nom })} onClick={() => setLogsOf(ct.nom)}>{t("ct.logs")}</button>}
                          {caps.admin && on && ct.mode !== "application" && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("a11y.terminal_x", { v: ct.nom })} onClick={() => openTerminal(ct)}>{t("ct.terminal")}</button>}
                          {caps.admin && !on && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("a11y.start_x", { v: ct.nom })} onClick={() => act(startContainer, ct, t("ct.started"))}>{t("ct.start")}</button>}
                          {caps.admin && on && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("a11y.stop_x", { v: ct.nom })} onClick={() => stop(ct)}>{t("ct.stop")}</button>}
                          {caps.admin && !on && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("a11y.clone_x", { v: ct.nom })} onClick={() => clone(ct)}>{t("ct.clone")}</button>}
                          {caps.admin && !on && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("a11y.back_up_container_x", { v: ct.nom })} onClick={() => backup(ct)}>{t("ct.backup")}</button>}
                          {caps.admin && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_container_x", { v: ct.nom })} title={del} onClick={() => remove(ct)}><Trash2 size={15} aria-hidden="true" /></button>}
                        </div></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>
          )}
        </div>
      )}

      {caps.admin && (
        <Card title={t("ct.backups")} note={backups?.length || null} flush={Boolean(backups && backups.length)}>
          {!backups || backups.length === 0 ? <p className="nx-muted" style={{ margin: 0, fontSize: "var(--fs-13)" }}>{t("ct.backupsNone")}</p> : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ct.container")}</th><th scope="col">{t("ct.date")}</th><th scope="col" className="nx-num">{t("ct.size")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {backups.map((b) => (
                    <tr key={b.id}>
                      <td><StatusIndicator kind="task" wire={backupWire(b.statut)} /></td>
                      <th scope="row" className="nx-mono">{b.container_name}</th>
                      <td>{new Date(b.cree_le).toLocaleString(lang)}</td>
                      <td className="nx-num nx-mono">{b.taille_octets ? formatSizeMb(b.taille_octets / 1048576, lang) : "—"}</td>
                      <td><div className="nx-ra">
                        {b.statut === "termine" && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("a11y.restore_backup_x", { v: b.id })} onClick={() => restore(b)}>{t("ct.restore")}</button>}
                        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_backup_x", { v: b.id })} title={del} onClick={() => removeBackup(b)}><Trash2 size={15} aria-hidden="true" /></button>
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      )}
      <ContainerLogs name={logsOf} onClose={() => setLogsOf(null)} />
      <ActionsContextMenu ctx={ctx} label={(ct) => t("ctx.menuOf", { name: ct.nom })} entries={(ct) => {
        const on = ct.etat === "actif";
        const admin = { disabled: !caps.admin, reason: t("menu.reason.admin") };
        return [
          on ? { key: "terminal", icon: "terminal", label: t("ct.terminal"), run: () => openTerminal(ct), ...(ct.mode === "application" ? { disabled: true, reason: t("ct.noTerminalApp") } : admin) }
            : { key: "start", icon: "start", label: t("ct.start"), run: () => act(startContainer, ct, t("ct.started")), ...admin },
          on && { key: "stop", icon: "stop", label: t("ct.stop"), run: () => stop(ct), ...admin },
          ct.mode === "application" && { key: "logs", icon: "details", label: t("ct.logs"), run: () => setLogsOf(ct.nom) },
          "-",
          { key: "clone", icon: "clone", label: t("ct.clone"), run: () => clone(ct), disabled: !caps.admin || on, reason: !caps.admin ? t("menu.reason.admin") : t("menu.reason.mustStop") },
          { key: "backup", icon: "backup", label: t("ct.backup"), run: () => backup(ct), disabled: !caps.admin || on, reason: !caps.admin ? t("menu.reason.admin") : t("menu.reason.mustStop") },
          "-",
          { key: "delete", icon: "delete", label: del, danger: true, run: () => remove(ct), ...admin },
        ];
      }} />
    </>
  );
}
ContainersPage.ownHeader = true;

// What a Docker container's process printed (docker logs), refreshed every few seconds while open: the way to see
// why a container stopped by itself.
function ContainerLogs({ name, onClose }) {
  const t = useT();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!name) return undefined;
    let alive = true;
    setData(null); setError(null);
    const load = () => fetchContainerLogs(name).then((r) => { if (alive) { setData(r); setError(null); } }).catch((e) => alive && setError(errorMessage(e)));
    load();
    const id = setInterval(load, 3000);
    return () => { alive = false; clearInterval(id); };
  }, [name]);
  return (
    <SideDrawer open={Boolean(name)} title={t("ct.logsTitle", { name: name || "" })} onClose={onClose}>
      {error ? <div className="nx-bn" data-tone="danger" role="alert"><span className="nx-bn-t">{error}</span></div>
        : !data ? <Loading />
        : !data.disponible ? <p className="nx-muted">{t("ct.logsUnavailable")}</p>
        : (<>
          <p className="nx-muted" role="status" style={{ margin: 0 }}>{t(data.actif ? "ct.logsRunning" : "ct.logsStopped")}</p>
          <pre className="nx-mono" tabIndex={0} aria-label={t("ct.logsTitle", { name })} style={{ margin: 0, padding: "var(--space-3)", background: "var(--color-bg-sunken, var(--color-bg-surface))", border: "1px solid var(--color-border-subtle)", borderRadius: "var(--radius-md)", fontSize: "var(--fs-12)", whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "70vh", overflow: "auto" }}>
            {data.lignes.length ? data.lignes.join("\n") : t("ct.logsEmpty")}
          </pre>
        </>)}
    </SideDrawer>
  );
}
