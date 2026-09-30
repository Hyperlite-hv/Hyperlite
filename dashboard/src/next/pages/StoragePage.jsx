import { Fragment, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Layers, Plus, Trash2 } from "lucide-react";
import { createStoragePool, fetchStorageSupport, deleteStoragePool, fetchSharedPools, renameStoragePool, fetchVolumes, createVolume, deleteVolume, checkPoolPermissions } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { formatSizeGb } from "../lib/format";
import { errorMessage } from "../lib/errors";
import { useIntent } from "../lib/intents";
import StatusIndicator from "../components/StatusIndicator";
import { ActionsContextMenu, useContextTarget } from "../components/ContextMenu";
import { PageHeader, Meter, Chip, SideDrawer, Field, Empty, TableWrap } from "../components/ui";
import { InlineError } from "../components/States";
import { promptText } from "../../store/usePromptStore";
import { askNewName } from "../lib/rename";

const EMPTY = { name: "", type: "dir", node: "local", scope: "tous", chosen: [], path: "", nfs_host: "", nfs_export_path: "", nfs_version: "4.2", size_gb: "20", iscsi_host: "", iscsi_port: "3260", iscsi_target: "", chap_user: "", chap_password: "" };
const IQN_RE = /^(iqn\.\d{4}-\d{2}\.[a-z0-9][a-z0-9.-]*(:[A-Za-z0-9._:-]{1,200})?|eui\.[0-9A-Fa-f]{16})$/;
// The same rules as the API (app/routers/storage.py, app/core/iscsi.py), checked before the request.
const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;
const HOST_RE = /^[A-Za-z0-9][A-Za-z0-9.:_-]{0,253}$/;
const ABS_PATH_RE = /^\/[A-Za-z0-9/_.-]{0,255}$/;
const CHAP_USER_RE = /^[A-Za-z0-9._@:-]{1,64}$/;
const typeLabel = (type) => ({ netfs: "NFS", zfs: "ZFS", iscsi: "iSCSI" }[type] || type);
// Versions the mount is forced to (never negotiated, see app/routers/storage.py::_build_pool_xml).
const NFS_VERSIONS = ["4.2", "4.1", "4.0", "4", "3"];
// An example name per kind of pool (shown as "e.g. …": an empty field must never look filled in).
const SHARED = ["netfs", "iscsi"];
const NAME_EXAMPLE = { dir: "local-ssd", netfs: "nas-vms", zfs: "zfs-fast", iscsi: "san-vms" };

// Error key per field for the chosen kind of pool, or none: only the fields that kind uses are checked.
export function poolFormErrors(form) {
  const e = {};
  const req = (v) => !String(v ?? "").trim();
  if (!NAME_RE.test(form.name)) e.name = req(form.name) ? "stor.e.required" : "stor.e.name";
  if (form.type === "dir" && form.path.trim() && !ABS_PATH_RE.test(form.path.trim())) e.path = "stor.e.absPath";
  if (form.type === "netfs") {
    if (req(form.nfs_host)) e.nfs_host = "stor.e.required"; else if (!HOST_RE.test(form.nfs_host.trim())) e.nfs_host = "stor.e.host";
    if (req(form.nfs_export_path)) e.nfs_export_path = "stor.e.required"; else if (!ABS_PATH_RE.test(form.nfs_export_path.trim())) e.nfs_export_path = "stor.e.absPath";
  }
  // Shared storage (NFS, iSCSI) can go on several nodes, as on Proxmox: every node, or the ones chosen.
  if (SHARED.includes(form.type) && form.scope === "choix" && !form.chosen.length) e.chosen = "stor.e.nodes";
  if (form.type === "zfs") {
    if (form.node !== "local") e.type = "stor.e.zfsLocal";
    const n = Number(form.size_gb);
    if (!Number.isInteger(n) || n < 1 || n > 4096) e.size_gb = "stor.e.size";
  }
  if (form.type === "iscsi") {
    if (req(form.iscsi_host)) e.iscsi_host = "stor.e.required"; else if (!HOST_RE.test(form.iscsi_host.trim())) e.iscsi_host = "stor.e.host";
    const port = Number(form.iscsi_port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) e.iscsi_port = "stor.e.port";
    if (req(form.iscsi_target)) e.iscsi_target = "stor.e.required"; else if (!IQN_RE.test(form.iscsi_target.trim())) e.iscsi_target = "stor.iscsiTargetBad";
    // CHAP is a user AND a password, or neither.
    const user = form.chap_user.trim();
    if (user && !CHAP_USER_RE.test(user)) e.chap_user = "stor.e.chapUser";
    else if (user && !form.chap_password) e.chap_password = "stor.e.chapBoth";
    else if (!user && form.chap_password) e.chap_user = "stor.e.chapBoth";
  }
  return e;
}

function CreatePoolDrawer({ open, onClose }) {
  const t = useT();
  const { nodes, refreshAll, pushToast } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, refreshAll: s.refreshAll, pushToast: s.pushToast })));
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [support, setSupport] = useState(null);
  useEffect(() => { if (open) fetchStorageSupport().then(setSupport).catch(() => setSupport(null)); }, [open]);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const shared = SHARED.includes(form.type) && nodes.length > 1;
  const scope = shared ? form.scope : "un";
  const targets = scope === "tous" ? nodes.map((n) => n.id) : scope === "choix" ? form.chosen : [form.node];
  // The local host only: a remote node reports its own error when the pool is created.
  const local = targets.includes("local");
  const blocker = !local || !support ? null
    : form.type === "netfs" && support.nfs !== "ok" ? t("stor.nfsNoClient")
    : form.type === "zfs" && support.zfs !== "ok" ? t(`stor.zfs.${support.zfs}`)
    : form.type === "iscsi" && support.iscsi !== "ok" ? t("stor.iscsiNoInitiator")
    : null;
  const TYPES = [["dir", t("stor.type.dir")], ["netfs", t("stor.type.netfs")], ["zfs", "ZFS"], ["iscsi", "iSCSI"]];
  const errors = poolFormErrors(form);
  // Shown after a first attempt; a value already typed in the wrong format is flagged at once.
  const err = (k) => (errors[k] && (attempted || (errors[k] !== "stor.e.required" && errors[k] !== "stor.e.chapBoth" && String(form[k] ?? "").trim())) ? t(errors[k]) : null);
  const ex = (v) => t("stor.example", { v });
  const close = () => { setAttempted(false); onClose(); };

  async function create() {
    if (Object.keys(errors).length) { setAttempted(true); return; }
    setBusy(true);
    try {
      const payload = form.type === "dir" ? { name: form.name, type: "dir", path: form.path.trim() || null }
        : form.type === "netfs" ? { name: form.name, type: "netfs", nfs_host: form.nfs_host.trim(), nfs_export_path: form.nfs_export_path.trim(), nfs_version: form.nfs_version }
        : form.type === "iscsi" ? { name: form.name, type: "iscsi", iscsi_host: form.iscsi_host.trim(), iscsi_port: Number(form.iscsi_port), iscsi_target: form.iscsi_target.trim(), chap_user: form.chap_user.trim() || null, chap_password: form.chap_user.trim() ? form.chap_password : null }
        : { name: form.name, type: "zfs", size_gb: Number(form.size_gb) };
      const where = scope === "tous" ? { tous_les_noeuds: true } : scope === "choix" ? { noeuds: form.chosen } : {};
      // "local" is the frontend sentinel of the local host: the backend only accepts registered remote nodes.
      const created = await createStoragePool({ ...payload, ...where }, scope === "un" && form.node !== "local" ? form.node : undefined);
      const nodeName = (id) => nodes.find((n) => n.id === id)?.nom || id;
      if (created?.resultats) {
        // One line per node: created, already there, or why it failed there (the others went on).
        const ok = created.resultats.filter((r) => r.etat !== "echec");
        const failed = created.resultats.filter((r) => r.etat === "echec");
        pushToast({ kind: "success", title: t("stor.createdOn", { name: form.name, n: ok.length }), message: ok.map((r) => `${nodeName(r.noeud)}${r.etat === "existe" ? ` (${t("stor.alreadyThere")})` : ""}`).join(", ") });
        if (failed.length) pushToast({ kind: "error", title: t("stor.failedOn", { n: failed.length }), message: failed.map((r) => `${nodeName(r.noeud)} : ${r.detail}`).join("\n"), duration: Infinity });
        for (const r of created.resultats) if (r.avertissement) pushToast({ kind: "error", title: `${t("stor.nfsPermTitle")} · ${nodeName(r.noeud)}`, message: r.avertissement, duration: Infinity });
      } else {
        pushToast({ kind: "success", title: t("stor.created"), message: form.name });
        // Mounted is not enough: an export with root_squash leaves QEMU unable to open its disks there.
        if (created?.avertissement) pushToast({ kind: "error", title: t("stor.nfsPermTitle"), message: created.avertissement, duration: Infinity });
      }
      setForm(EMPTY); close(); refreshAll();
    } catch (er) { pushToast({ kind: "error", title: t("stor.createFailed"), message: errorMessage(er) }); }
    finally { setBusy(false); }
  }
  return (
    <SideDrawer open={open} title={t("stor.createPool")} onClose={close} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={close} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !!blocker} onClick={create}>{busy ? t("stor.creating") : t("stor.createPool")}</button>
    </>}>
      <p className="nx-hint" style={{ margin: 0 }}>{t("stor.requiredNote")}</p>
      <Field label={t("stor.poolName")} hint={t("stor.nameHelp")} error={err("name")}>{(p) => <input {...p} className="nx-inp" aria-label={t("a11y.pool_name")} value={form.name} onChange={set("name")} placeholder={ex(NAME_EXAMPLE[form.type])} />}</Field>
      <div className="nx-f">
        <span className="nx-f-label" id="pool-type">{t("stor.poolType")}</span>
        <div className="nx-seg2" role="group" aria-labelledby="pool-type">
          {TYPES.map(([v, label]) => {
            const off = v === "zfs" && form.node !== "local"; // ZFS is managed on this host only (the API refuses it elsewhere)
            return <button key={v} type="button" aria-pressed={form.type === v} aria-disabled={off || undefined} title={off ? t("stor.e.zfsLocal") : undefined} onClick={() => !off && setForm((f) => ({ ...f, type: v }))}>{label}</button>;
          })}
        </div>
        {err("type") && <span className="nx-f-h is-error" role="alert">{err("type")}</span>}
      </div>
      {shared ? (
        <fieldset className="nx-fieldset">
          <legend>{t("stor.nodes")}</legend>
          {[["tous", t("stor.scope.all")], ["un", t("stor.scope.one")], ["choix", t("stor.scope.some")]].map(([v, label]) => (
            <label key={v} className="nx-check"><input type="radio" name="pool-scope" checked={form.scope === v} onChange={() => setForm((f) => ({ ...f, scope: v }))} /> {label}</label>
          ))}
          {form.scope === "un" && <Field label={t("ns.node")}>{(p) => <select {...p} className="nx-inp" aria-label={t("a11y.node")} value={form.node} onChange={set("node")}>{nodes.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}</select>}</Field>}
          {form.scope === "choix" && (
            <div role="group" aria-label={t("stor.nodes")} style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2) var(--space-4)", paddingInlineStart: "var(--space-5)" }}>
              {nodes.map((n) => <label key={n.id} className="nx-check"><input type="checkbox" checked={form.chosen.includes(n.id)} onChange={(e) => setForm((f) => ({ ...f, chosen: e.target.checked ? [...f.chosen, n.id] : f.chosen.filter((x) => x !== n.id) }))} /> {n.nom}</label>)}
            </div>
          )}
          {err("chosen") && <span className="nx-f-h is-error" role="alert">{err("chosen")}</span>}
          <span className="nx-f-h">{t(form.scope === "tous" ? "stor.scope.allHelp" : "stor.scope.help")}</span>
        </fieldset>
      ) : (
        <Field label={t("ns.node")}>{(p) => <select {...p} className="nx-inp" aria-label={t("a11y.node")} value={form.node} onChange={set("node")}>{nodes.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}</select>}</Field>
      )}
      {blocker && <div className="nx-bn" data-tone="warning" role="status"><span className="nx-bn-t">{blocker}</span></div>}
      {form.type === "dir" && <Field label={t("stor.path")} hint={t("stor.pathHelp")} error={err("path")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.local_path_optional")} value={form.path} onChange={set("path")} placeholder={ex("/var/lib/libvirt/hyperlite-pools/…")} />}</Field>}
      {form.type === "netfs" && <>
        <Field label={t("stor.nfsHost")} error={err("nfs_host")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.nfs_server_host")} value={form.nfs_host} onChange={set("nfs_host")} placeholder={ex("192.168.1.10")} />}</Field>
        <Field label={t("stor.nfsPath")} hint={t("stor.nfsPathHelp")} error={err("nfs_export_path")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.exported_path")} value={form.nfs_export_path} onChange={set("nfs_export_path")} placeholder={ex("/srv/share")} />}</Field>
        <Field label={t("stor.nfsVersion")} hint={t("stor.nfsVersionHelp")}>{(p) => (
          <select {...p} className="nx-inp" value={form.nfs_version} onChange={set("nfs_version")}>
            {NFS_VERSIONS.map((v) => <option key={v} value={v}>{`NFSv${v}`}{v === "4.2" ? ` (${t("stor.nfsDefault")})` : ""}</option>)}
          </select>
        )}</Field>
      </>}
      {form.type === "iscsi" && <>
        {local && support?.iscsi_initiator && (
          <div className="nx-bn" data-tone="info" role="note"><span className="nx-bn-t">{t("stor.iscsiInitiator")}<br /><code className="nx-mono" style={{ userSelect: "all" }}>{support.iscsi_initiator}</code></span></div>
        )}
        <div className="nx-formgrid">
          <Field label={t("stor.iscsiHost")} error={err("iscsi_host")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.iscsi_host} onChange={set("iscsi_host")} placeholder={ex("192.168.1.20")} />}</Field>
          <Field label={t("stor.iscsiPort")} error={err("iscsi_port")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min="1" max="65535" value={form.iscsi_port} onChange={set("iscsi_port")} />}</Field>
        </div>
        <Field label={t("stor.iscsiTarget")} hint={t("stor.iscsiTargetHelp")} error={err("iscsi_target")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.iscsi_target} onChange={set("iscsi_target")} placeholder={ex("iqn.2005-10.org.freenas.ctl:vms")} />}</Field>
        <fieldset className="nx-fieldset">
          <legend>{t("stor.chap")}</legend>
          <p className="nx-hint" style={{ margin: 0 }}>{t("stor.chapHelp")}</p>
          <div className="nx-formgrid">
            <Field label={t("stor.chapUser")} error={err("chap_user")}>{(p) => <input {...p} className="nx-inp nx-mono" autoComplete="off" value={form.chap_user} onChange={set("chap_user")} />}</Field>
            <Field label={t("stor.chapPassword")} error={err("chap_password")}>{(p) => <input {...p} className="nx-inp" type="password" autoComplete="new-password" value={form.chap_password} onChange={set("chap_password")} />}</Field>
          </div>
        </fieldset>
        <p className="nx-hint">{t("stor.iscsiHelp")}</p>
      </>}
      {form.type === "zfs" && <Field label={t("stor.zfsSize")} hint={t("stor.zfsHelp")} error={err("size_gb")} unit="Go">{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.size_gb_loopback_file")} type="number" min="1" max="4096" value={form.size_gb} onChange={set("size_gb")} />}</Field>}
    </SideDrawer>
  );
}

// Storage: the pools only (the ISO images live in the Library). Same safeguards as the historical screen:
// the default pool is never removable; removing a directory/NFS pool only removes its definition.
export default function StoragePage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { pools, nodes, refreshAll, pushToast } = useInfraStore(useShallow((s) => ({ pools: s.storagePools, nodes: s.nodes, refreshAll: s.refreshAll, pushToast: s.pushToast })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState(null);
  const [volumes, setVolumes] = useState({});
  const [volumeErrors, setVolumeErrors] = useState({});
  useIntent("pool", () => caps.admin && setCreating(true));
  const ctx = useContextTarget(); // right click on a pool: its actions
  // Pools declared for several nodes: marked in the list, and removed from every node together.
  const [shared, setShared] = useState({});
  useEffect(() => { fetchSharedPools().then((r) => setShared(Object.fromEntries((Array.isArray(r) ? r : []).map((x) => [x.nom, x])))).catch(() => setShared({})); }, [pools]);

  async function loadVolumes(p) {
    const key = `${p.node}:${p.nom}`;
    setVolumeErrors((x) => ({ ...x, [key]: null }));
    try { const v = await fetchVolumes(p.nom); setVolumes((x) => ({ ...x, [key]: Array.isArray(v) ? v : [] })); }
    // A failed read is not an empty pool: say so, with a retry.
    catch (e) { setVolumes((x) => ({ ...x, [key]: undefined })); setVolumeErrors((x) => ({ ...x, [key]: errorMessage(e) })); }
  }
  async function toggleVolumes(p) {
    const key = `${p.node}:${p.nom}`;
    setOpen(open === key ? null : key);
    if (open !== key && p.node === "local" && !volumes[key]) loadVolumes(p);
  }
  // Volumes are files (or zvols) of a pool of this host; an iSCSI pool's LUNs are managed on the storage side.
  const volumesEditable = (p) => caps.admin && p.node === "local" && p.type !== "iscsi";
  async function newVolume(p) {
    const name = await promptText({ title: t("stor.newVolumeTitle", { name: p.nom }), label: t("stor.volName"), confirmLabel: t("wz.next"), validate: (v) => (/^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/.test(v.replace(/\.qcow2$/, "")) ? "" : t("wz.e.name")) });
    if (!name) return;
    const size = await promptText({ title: t("stor.newVolumeTitle", { name: p.nom }), label: t("stor.volSize"), defaultValue: "10", confirmLabel: t("stor.createVolume"), validate: (v) => (/^[1-9][0-9]{0,5}$/.test(v) ? "" : t("stor.volSizeRule")) });
    if (!size) return;
    try { await createVolume(p.nom, name, Number(size)); pushToast({ kind: "success", title: t("stor.volCreated"), message: name }); loadVolumes(p); refreshAll(); }
    catch (e) { pushToast({ kind: "error", title: t("stor.volCreateFailed"), message: errorMessage(e) }); }
  }
  async function removeVolume(p, v) {
    const ok = await confirmAction({ title: t("stor.volDeleteTitle", { name: v.nom }), message: t("stor.volDeleteMsg", { pool: p.nom }), confirmLabel: t("action.confirm"), danger: true });
    if (!ok) return;
    try { await deleteVolume(p.nom, v.nom); pushToast({ kind: "success", title: t("stor.volDeleted"), message: v.nom }); loadVolumes(p); refreshAll(); }
    catch (e) { pushToast({ kind: "error", title: t("stor.volDeleteFailed"), message: errorMessage(e) }); }
  }
  async function checkPerm(p) {
    try {
      const r = await checkPoolPermissions(p.nom);
      if (r.ok) pushToast({ kind: "success", title: t("stor.nfsPermOk"), message: p.nom });
      else pushToast({ kind: "error", title: t("stor.nfsPermTitle"), message: r.message, duration: Infinity });
    } catch (e) { pushToast({ kind: "error", title: t("stor.nfsPermFailed"), message: errorMessage(e) }); }
  }
  async function renamePool(p) {
    const everywhere = Boolean(shared[p.nom]);
    const name = await askNewName(t, { title: t("rn.poolTitle", { name: p.nom }), message: t(everywhere ? "rn.poolSharedMsg" : "rn.poolMsg"), current: p.nom });
    if (!name) return;
    try {
      const r = await renameStoragePool(p.nom, name, p.node, everywhere);
      const failed = (r?.resultats || []).filter((x) => x.etat === "echec");
      if (failed.length) pushToast({ kind: "error", title: t("rn.failedOn", { n: failed.length }), message: failed.map((x) => `${nodes.find((n) => n.id === x.noeud)?.nom || x.noeud} : ${x.detail}`).join("\n"), duration: Infinity });
      else pushToast({ kind: "success", title: t("rn.done"), message: `${p.nom} → ${name}` });
      refreshAll();
    } catch (err) { pushToast({ kind: "error", title: t("rn.failed"), message: errorMessage(err) }); }
  }
  async function removePool(p) {
    if (p.nom === "default") return;
    const fsBacked = p.type === "dir" || p.type === "netfs";
    const everywhere = Boolean(shared[p.nom]);
    const base = p.type === "iscsi" ? t("stor.confirmIscsi") : fsBacked ? t("stor.confirmFs", { name: p.nom }) : t("stor.confirmEmpty", { name: p.nom });
    const message = everywhere ? `${t("stor.confirmEverywhere")} ${base}` : base;
    const ok = await confirmAction({ title: t(everywhere ? "stor.confirmTitleEverywhere" : "stor.confirmTitle", { name: p.nom }), message, confirmLabel: t("action.confirm"), danger: true });
    if (!ok) return;
    try {
      const r = await deleteStoragePool(p.nom, p.node === "local" ? undefined : p.node, fsBacked, everywhere);
      const failed = (r?.resultats || []).filter((x) => x.etat === "echec");
      if (failed.length) pushToast({ kind: "error", title: t("stor.deleteFailedOn", { n: failed.length }), message: failed.map((x) => `${nodes.find((n) => n.id === x.noeud)?.nom || x.noeud} : ${x.detail}`).join("\n"), duration: Infinity });
      else pushToast({ kind: "success", title: t("stor.removed"), message: everywhere ? t("stor.removedEverywhere", { name: p.nom }) : p.nom });
      refreshAll();
    } catch (err) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(err) }); }
  }

  return (
    <>
      <PageHeader title={t("tab.storage")} count={pools.length} desc={t("stor.desc")}
        actions={caps.admin && <button type="button" className="nx-btn nx-btn--primary" onClick={() => setCreating(true)}><Plus size={15} aria-hidden="true" />{t("stor.createPool")}</button>} />
      <div className="nx-card2 nx-card2--flush">
        {pools.length === 0 ? <Empty icon={Layers} title={t("ov.noPools")} text={t("stor.noneHelp")} /> : (
          <TableWrap>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("stor.pool")}</th><th scope="col">{t("ns.node")}</th><th scope="col">{t("stor.type")}</th><th scope="col">{t("stor.usage")}</th><th scope="col" className="nx-num">{t("stor.capacity")}</th><th scope="col" className="nx-num">{t("stor.free")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {pools.map((p) => {
                  const key = `${p.node}:${p.nom}`;
                  const r = p.capacite_go ? ((p.capacite_go - (p.disponible_go ?? p.capacite_go)) / p.capacite_go) * 100 : null;
                  const vols = volumes[key];
                  return (
                    <Fragment key={key}>
                      <tr className={ctx.is("pool", key) ? "is-ctx" : undefined} onContextMenu={ctx.open("pool", p, key)}>
                        <td><StatusIndicator kind="pool" wire={p.etat} /></td>
                        <th scope="row" className="nx-nm">{p.nom}{p.chemin && <small className="nx-mono">{p.chemin}</small>}</th>
                        <td className="nx-mono">{nodes.find((n) => n.id === p.node)?.nom || p.node}</td>
                        <td><Chip title={p.type === "zfs" ? t("stor.zfsLocal") : undefined}>{typeLabel(p.type)}{p.type === "netfs" && p.nfs_version ? ` v${p.nfs_version}` : ""}</Chip>
                          {shared[p.nom] && <> <Chip tone="info" title={shared[p.nom].tous_les_noeuds ? t("stor.sharedAllHelp") : t("stor.sharedSomeHelp", { nodes: shared[p.nom].noeuds.map((id) => nodes.find((n) => n.id === id)?.nom || id).join(", ") })}>{shared[p.nom].tous_les_noeuds ? t("stor.sharedAll") : t("stor.shared")}</Chip></>}</td>
                        <td><Meter value={r} label={`${p.nom} ${t("stor.usage")}`} /></td>
                        <td className="nx-num nx-mono">{formatSizeGb(p.capacite_go, lang) ?? "—"}</td>
                        <td className="nx-num nx-mono">{formatSizeGb(p.disponible_go, lang) ?? "—"}</td>
                        <td><div className="nx-ra">
                          <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-expanded={open === key} aria-label={t("stor.volumesOf", { name: p.nom })} onClick={() => toggleVolumes(p)}>{t("stor.volumes")}</button>
                          {caps.admin && p.nom !== "default" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_pool_x", { v: p.nom })} title={t("menu.delete").replace("…", "")} onClick={() => removePool(p)}><Trash2 size={15} aria-hidden="true" /></button>}
                        </div></td>
                      </tr>
                      {open === key && (
                        <tr><td colSpan={8} className="nx-detailcell">
                          {p.node !== "local" ? <span className="nx-muted">{t("stor.volLocalOnly")}</span> : volumeErrors[key] ? <InlineError message={volumeErrors[key]} onRetry={() => loadVolumes(p)} /> : vols == null ? <span className="nx-muted">{t("loading")}</span> : (
                            <>
                              {vols.length === 0 ? <span className="nx-muted">{t("stor.noVolumes")}</span> : (
                                <ul className="nx-list nx-list--vols">{vols.map((v) => (
                                  <li key={v.nom}><span className="nx-mono">{v.nom}</span><span className="nx-mono nx-muted">{formatSizeGb(v.capacite_go, lang)}</span><span className="nx-muted">{v.utilise ? t("stor.inUse") : t("stor.free")}</span>
                                    {volumesEditable(p) && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" disabled={v.utilise} title={v.utilise ? t("stor.volInUse") : t("menu.delete").replace("…", "")} aria-label={t("stor.volDeleteAria", { name: v.nom })} onClick={() => removeVolume(p, v)}><Trash2 size={14} aria-hidden="true" /></button>}
                                  </li>
                                ))}</ul>
                              )}
                              {volumesEditable(p) && <div style={{ marginTop: "var(--space-2)" }}><button type="button" className="nx-btn nx-btn--sm" onClick={() => newVolume(p)}><Plus size={14} aria-hidden="true" />{t("stor.createVolume")}</button></div>}
                            </>
                          )}
                        </td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        )}
      </div>
      <CreatePoolDrawer open={creating} onClose={() => setCreating(false)} />
      <ActionsContextMenu ctx={ctx} label={(p) => t("ctx.menuOf", { name: p.nom })} entries={(p) => [
        { key: "volumes", icon: "volumes", label: t("stor.volumes"), run: () => toggleVolumes(p) },
        p.chemin && { key: "path", icon: "copy", label: t("ctx.copyPath"), run: () => navigator.clipboard?.writeText(p.chemin) },
        p.type === "netfs" && { key: "perm", icon: "admin", label: t("stor.nfsPermCheck"), run: () => checkPerm(p), disabled: !caps.admin || p.etat !== "actif", reason: !caps.admin ? t("menu.reason.admin") : t("stor.nfsPermInactive") },
        "-",
        { key: "rename", icon: "rename", label: t("vx.renameMenu"), run: () => renamePool(p), disabled: !caps.admin || p.nom === "default" || p.type === "zfs",
          reason: !caps.admin ? t("menu.reason.admin") : p.type === "zfs" ? t("rn.zfsNo") : t("rn.defaultNo") },
        { key: "delete", icon: "delete", label: t("vx.delete"), danger: true, run: () => removePool(p),
          disabled: !caps.admin || p.nom === "default", reason: !caps.admin ? t("menu.reason.admin") : t("ctx.defaultPool") },
      ]} />
    </>
  );
}
StoragePage.ownHeader = true;
