import { useCallback, useEffect, useState } from "react";
import {
  fetchContainers, createContainer, startContainer, stopContainer, deleteContainer, searchDockerHub,
  cloneContainer, fetchContainerBackups, createContainerBackup, deleteContainerBackup, restoreContainerBackup,
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
import { EmptyState, ErrorState } from "../components/States";

const DEFAULT_FORM = { name: "", vcpu: 1, memory_mb: 512, username: "", password: "", network: "default", image: "" };
const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;

// Any Docker Hub / OCI reference already worked through the free-text field; the gallery only
// presents the common ones. An empty key is the local Debian 12 base (fastest to create).
const GALLERY = [
  ["", "Debian 12", "ct.g.debian"], ["ubuntu:24.04", "Ubuntu", "ct.g.ubuntu"], ["alpine:3.19", "Alpine", "ct.g.alpine"],
  ["nginx:latest", "Nginx", "ct.g.nginx"], ["httpd:latest", "Apache", "ct.g.apache"], ["postgres:16", "PostgreSQL", "ct.g.db"],
  ["mysql:8", "MySQL", "ct.g.db"], ["mariadb:11", "MariaDB", "ct.g.db"], ["mongo:latest", "MongoDB", "ct.g.doc"],
  ["redis:latest", "Redis", "ct.g.cache"], ["node:22", "Node.js", "ct.g.runtime"], ["python:3.12", "Python", "ct.g.runtime"],
  ["wordpress:latest", "WordPress", "ct.g.cms"],
];

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
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);

  const reload = useCallback(async () => {
    try { const r = await fetchContainers(); setContainers(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  const reloadBackups = useCallback(() => { fetchContainerBackups().then((b) => setBackups(Array.isArray(b) ? b : [])).catch(() => setBackups([])); }, []);
  useEffect(() => { reload(); if (caps.admin) reloadBackups(); }, [reload, reloadBackups, caps.admin]);
  useEffect(() => {
    if (!creating && !(containers || []).length) return undefined;
    const id = setInterval(reload, 6000);
    return () => clearInterval(id);
  }, [creating, containers, reload]);
  useEffect(() => {
    if (!query.trim()) return undefined;
    const id = setTimeout(() => { searchDockerHub(query).then((r) => setResults(Array.isArray(r) ? r : [])).catch(() => setResults([])); }, 400);
    return () => clearTimeout(id);
  }, [query]);

  const fail = (title) => (e) => pushToast({ kind: "error", title, message: errorMessage(e) });
  const set = (k, num) => (e) => setForm((f) => ({ ...f, [k]: num ? Number(e.target.value) : e.target.value }));
  const nameCheck = (v) => (NAME_RE.test(v) ? "" : t("ct.nameRule"));

  async function create(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await createContainer(form);
      pushToast({ kind: "success", title: t("ct.created"), message: `${form.name}: ${t("ct.building")}` });
      setCreating(false); setForm(DEFAULT_FORM); setQuery(""); setResults([]);
      await reload();
    } catch (err) { fail(t("ct.createFailed"))(err); } finally { setBusy(false); }
  }
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
  const openTerminal = (ct) => window.open(`/container-terminal/${encodeURIComponent(ct.nom)}`, `hyperlite-ct-terminal-${ct.nom}`, "width=1000,height=700,noopener");
  const pick = (image) => { setForm((f) => ({ ...f, image })); setQuery(""); setResults([]); };

  if (error && containers == null) return <ErrorState message={error} onRetry={reload} />;
  const list = containers || [];
  const del = t("menu.delete").replace("…", "");

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="ct-list">
        <div className="nx-cardhead">
          <h2 id="ct-list">{t("inv.containers")} <span className="nx-count">{containers ? list.length : "…"}</span></h2>
          {caps.admin && <button type="button" className="nx-btn nx-btn--primary" aria-expanded={creating} onClick={() => setCreating((c) => !c)}>{t("ct.create")}</button>}
        </div>
        <p className="nx-muted" style={{ marginTop: 0, maxWidth: "60ch" }}>{t("ct.intro")}</p>

        {creating && (
          <form className="nx-form" onSubmit={create}>
            <fieldset className="nx-fieldset">
              <legend>{t("ct.image")}</legend>
              <div className="nx-gallery">
                {GALLERY.map(([key, label, desc]) => (
                  <button type="button" key={label} className="nx-tile-pick" aria-pressed={form.image === key} onClick={() => pick(key)}>
                    <strong>{label}</strong><span className="nx-muted">{t(desc)}</span>
                  </button>
                ))}
              </div>
              <label>{t("ct.otherImage")}
                <input className="nx-input" aria-label="Docker Hub image" value={query} placeholder="traefik, ghcr.io/foo/bar:tag" onChange={(e) => { setQuery(e.target.value); setForm((f) => ({ ...f, image: e.target.value })); }} />
              </label>
              {results.length > 0 && (
                <ul className="nx-list nx-list--vols" aria-label={t("ct.results")}>
                  {results.map((r) => (
                    <li key={r.nom}>
                      <button type="button" className="nx-link" onClick={() => pick(`${r.nom}:latest`)}>{r.nom}</button>
                      <span className="nx-muted">{r.officielle ? `${t("ct.official")} · ` : ""}{r.description}</span>
                      <span className="nx-mono nx-muted">★ {r.etoiles}</span>
                    </li>
                  ))}
                </ul>
              )}
              {form.image && !GALLERY.some((g) => g[0] === form.image) && <span className="nx-hint">{t("ct.selected")} <span className="nx-mono">{form.image}</span></span>}
            </fieldset>
            <div className="nx-formgrid">
              <label>{t("ct.name")}<input className="nx-input" aria-label="Name" required value={form.name} onChange={set("name")} /></label>
              <label>vCPU<input className="nx-input" aria-label="vCPU" type="number" min={1} max={16} value={form.vcpu} onChange={set("vcpu", true)} /></label>
              <label>{t("ct.ram")}<input className="nx-input" aria-label="RAM (MB)" type="number" min={128} step={128} value={form.memory_mb} onChange={set("memory_mb", true)} /></label>
              <label>{t("ct.network")}<input className="nx-input" aria-label="Network" value={form.network} onChange={set("network")} /></label>
              <label>{t("ct.user")}<input className="nx-input" aria-label="User" required value={form.username} onChange={set("username")} autoComplete="off" /></label>
              <label>{t("ct.password")}<input className="nx-input" aria-label="Password" type="password" required value={form.password} onChange={set("password")} autoComplete="new-password" /></label>
            </div>
            <div className="nx-formactions">
              <button type="button" className="nx-btn" onClick={() => setCreating(false)}>{t("action.cancel")}</button>
              <button type="submit" className="nx-btn nx-btn--primary" disabled={busy || !form.name || !form.username || !form.password}>{busy ? t("stor.creating") : t("action.create")}</button>
            </div>
          </form>
        )}

        {containers == null ? <p className="nx-muted" role="status">{t("loading")}</p> : list.length === 0 ? (
          <EmptyState title={t("ct.none")} help={caps.admin ? t("ct.noneHelp") : t("ct.noneObserver")} />
        ) : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ct.name")}</th><th scope="col" className="nx-num">vCPU</th><th scope="col" className="nx-num">{t("ct.memory")}</th><th scope="col">IP</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {list.map((ct) => {
                  const on = ct.etat === "actif";
                  return (
                    <tr key={ct.nom}>
                      <td><StatusIndicator kind="vm" wire={on ? "actif" : "arrete"} /></td>
                      <th scope="row" className="nx-mono">{ct.nom}</th>
                      <td className="nx-num nx-mono">{ct.vcpu}</td>
                      <td className="nx-num nx-mono">{formatSizeMb(ct.memoire_mo, lang)}</td>
                      <td className="nx-mono">{ct.ip || <span className="nx-muted">{t("ct.noIp")}</span>}</td>
                      <td className="nx-num nx-rowactions">
                        {caps.admin && on && <button type="button" className="nx-btn" aria-label={`Terminal ${ct.nom}`} onClick={() => openTerminal(ct)}>{t("ct.terminal")}</button>}
                        {caps.admin && !on && <button type="button" className="nx-btn" aria-label={`Start ${ct.nom}`} onClick={() => act(startContainer, ct, t("ct.started"))}>{t("ct.start")}</button>}
                        {caps.admin && on && <button type="button" className="nx-btn" aria-label={`Stop ${ct.nom}`} onClick={() => stop(ct)}>{t("ct.stop")}</button>}
                        {caps.admin && !on && <button type="button" className="nx-btn" aria-label={`Clone ${ct.nom}`} onClick={() => clone(ct)}>{t("ct.clone")}</button>}
                        {caps.admin && !on && <button type="button" className="nx-btn" aria-label={`Back up container ${ct.nom}`} onClick={() => backup(ct)}>{t("ct.backup")}</button>}
                        {caps.admin && <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete container ${ct.nom}`} onClick={() => remove(ct)}>{del}</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {caps.admin && backups && backups.length > 0 && (
        <section className="nx-card" aria-labelledby="ct-backups">
          <div className="nx-cardhead"><h2 id="ct-backups">{t("ct.backups")} <span className="nx-count">{backups.length}</span></h2></div>
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ct.container")}</th><th scope="col">{t("ct.date")}</th><th scope="col" className="nx-num">{t("ct.size")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {backups.map((b) => (
                  <tr key={b.id}>
                    <td><StatusIndicator kind="task" wire={backupWire(b.statut)} /></td>
                    <th scope="row" className="nx-mono">{b.container_name}</th>
                    <td>{new Date(b.cree_le).toLocaleString(lang)}</td>
                    <td className="nx-num nx-mono">{b.taille_octets ? formatSizeMb(b.taille_octets / 1048576, lang) : "—"}</td>
                    <td className="nx-num nx-rowactions">
                      {b.statut === "termine" && <button type="button" className="nx-btn" aria-label={`Restore backup #${b.id}`} onClick={() => restore(b)}>{t("ct.restore")}</button>}
                      <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete backup #${b.id}`} onClick={() => removeBackup(b)}>{del}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
