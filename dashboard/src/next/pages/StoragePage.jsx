import { Fragment, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Layers, Plus, Trash2 } from "lucide-react";
import { createStoragePool, fetchStorageSupport, deleteStoragePool, fetchVolumes } from "../../api/client";
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

const EMPTY = { name: "", type: "dir", node: "local", path: "", nfs_host: "", nfs_export_path: "", nfs_version: "4.2", size_gb: "20", iscsi_host: "", iscsi_port: "3260", iscsi_target: "", chap_user: "", chap_password: "" };
const IQN_RE = /^(iqn\.\d{4}-\d{2}\.[a-z0-9][a-z0-9.-]*(:[A-Za-z0-9._:-]{1,200})?|eui\.[0-9A-Fa-f]{16})$/;
const typeLabel = (type) => ({ netfs: "NFS", zfs: "ZFS", iscsi: "iSCSI" }[type] || type);
// Versions the mount is forced to (never negotiated, see app/routers/storage.py::_build_pool_xml).
const NFS_VERSIONS = ["4.2", "4.1", "4.0", "4", "3"];

function CreatePoolDrawer({ open, onClose }) {
  const t = useT();
  const { nodes, refreshAll, pushToast } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, refreshAll: s.refreshAll, pushToast: s.pushToast })));
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [support, setSupport] = useState(null);
  useEffect(() => { if (open) fetchStorageSupport().then(setSupport).catch(() => setSupport(null)); }, [open]);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  // The local host only: a remote node reports its own error when the pool is created.
  const local = form.node === "local";
  const blocker = !local || !support ? null
    : form.type === "netfs" && support.nfs !== "ok" ? t("stor.nfsNoClient")
    : form.type === "zfs" && support.zfs !== "ok" ? t(`stor.zfs.${support.zfs}`)
    : form.type === "iscsi" && support.iscsi !== "ok" ? t("stor.iscsiNoInitiator")
    : null;
  const TYPES = [["dir", t("stor.type.dir")], ["netfs", t("stor.type.netfs")], ["zfs", "ZFS"], ["iscsi", "iSCSI"]];
  const iscsiValid = form.iscsi_host && IQN_RE.test(form.iscsi_target) && Number(form.iscsi_port) >= 1 && (!form.chap_user || form.chap_password);
  const valid = form.name && (form.type !== "netfs" || (form.nfs_host && form.nfs_export_path)) && (form.type !== "zfs" || Number(form.size_gb) >= 1) && (form.type !== "iscsi" || iscsiValid);

  async function create() {
    setBusy(true);
    try {
      const payload = form.type === "dir" ? { name: form.name, type: "dir", path: form.path || null }
        : form.type === "netfs" ? { name: form.name, type: "netfs", nfs_host: form.nfs_host, nfs_export_path: form.nfs_export_path, nfs_version: form.nfs_version }
        : form.type === "iscsi" ? { name: form.name, type: "iscsi", iscsi_host: form.iscsi_host, iscsi_port: Number(form.iscsi_port), iscsi_target: form.iscsi_target.trim(), chap_user: form.chap_user || null, chap_password: form.chap_user ? form.chap_password : null }
        : { name: form.name, type: "zfs", size_gb: Number(form.size_gb) };
      // "local" is the frontend sentinel of the local host: the backend only accepts registered remote nodes.
      await createStoragePool(payload, form.node === "local" ? undefined : form.node);
      pushToast({ kind: "success", title: t("stor.created"), message: form.name });
      setForm(EMPTY); onClose(); refreshAll();
    } catch (err) { pushToast({ kind: "error", title: t("stor.createFailed"), message: errorMessage(err) }); }
    finally { setBusy(false); }
  }
  return (
    <SideDrawer open={open} title={t("stor.createPool")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !valid || !!blocker} onClick={create}>{busy ? t("stor.creating") : t("stor.createPool")}</button>
    </>}>
      <Field label={t("stor.poolName")}>{(p) => <input {...p} className="nx-inp" aria-label={t("a11y.pool_name")} value={form.name} onChange={set("name")} placeholder="nfs-shared" />}</Field>
      <Field label={t("ns.node")}>{(p) => <select {...p} className="nx-inp" aria-label={t("a11y.node")} value={form.node} onChange={set("node")}>{nodes.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}</select>}</Field>
      <div className="nx-f">
        <span className="nx-f-label" id="pool-type">{t("stor.poolType")}</span>
        <div className="nx-seg2" role="group" aria-labelledby="pool-type">
          {TYPES.map(([v, label]) => <button key={v} type="button" aria-pressed={form.type === v} onClick={() => setForm((f) => ({ ...f, type: v }))}>{label}</button>)}
        </div>
      </div>
      {blocker && <div className="nx-bn" data-tone="warning" role="status"><span className="nx-bn-t">{blocker}</span></div>}
      {form.type === "dir" && <Field label={t("stor.path")} hint={t("stor.pathHelp")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.local_path_optional")} value={form.path} onChange={set("path")} placeholder="/var/lib/libvirt/hyperlite-pools/…" />}</Field>}
      {form.type === "netfs" && <>
        <Field label={t("stor.nfsHost")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.nfs_server_host")} value={form.nfs_host} onChange={set("nfs_host")} placeholder="192.168.1.10" />}</Field>
        <Field label={t("stor.nfsPath")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.exported_path")} value={form.nfs_export_path} onChange={set("nfs_export_path")} placeholder="/srv/share" />}</Field>
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
          <Field label={t("stor.iscsiHost")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.iscsi_host} onChange={set("iscsi_host")} placeholder="192.168.1.20" />}</Field>
          <Field label={t("stor.iscsiPort")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min="1" max="65535" value={form.iscsi_port} onChange={set("iscsi_port")} />}</Field>
        </div>
        <Field label={t("stor.iscsiTarget")} hint={t("stor.iscsiTargetHelp")} error={form.iscsi_target && !IQN_RE.test(form.iscsi_target.trim()) ? t("stor.iscsiTargetBad") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.iscsi_target} onChange={set("iscsi_target")} placeholder="iqn.2005-10.org.freenas.ctl:vms" />}</Field>
        <div className="nx-formgrid">
          <Field label={t("stor.chapUser")}>{(p) => <input {...p} className="nx-inp nx-mono" autoComplete="off" value={form.chap_user} onChange={set("chap_user")} />}</Field>
          <Field label={t("stor.chapPassword")}>{(p) => <input {...p} className="nx-inp" type="password" autoComplete="new-password" disabled={!form.chap_user} value={form.chap_password} onChange={set("chap_password")} />}</Field>
        </div>
        <p className="nx-hint">{t("stor.iscsiHelp")}</p>
      </>}
      {form.type === "zfs" && <Field label={t("stor.zfsSize")} hint={t("stor.zfsHelp")} unit="Go">{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.size_gb_loopback_file")} type="number" min="1" max="4096" value={form.size_gb} onChange={set("size_gb")} />}</Field>}
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
  useIntent("pool", () => caps.admin && setCreating(true));
  const ctx = useContextTarget(); // right click on a pool: its actions

  async function toggleVolumes(p) {
    const key = `${p.node}:${p.nom}`;
    setOpen(open === key ? null : key);
    if (open !== key && p.node === "local" && !volumes[key]) {
      try { const v = await fetchVolumes(p.nom); setVolumes((x) => ({ ...x, [key]: Array.isArray(v) ? v : [] })); }
      catch { setVolumes((x) => ({ ...x, [key]: [] })); }
    }
  }
  async function removePool(p) {
    if (p.nom === "default") return;
    const fsBacked = p.type === "dir" || p.type === "netfs";
    const message = p.type === "iscsi" ? t("stor.confirmIscsi") : fsBacked ? t("stor.confirmFs", { name: p.nom }) : t("stor.confirmEmpty", { name: p.nom });
    const ok = await confirmAction({ title: t("stor.confirmTitle", { name: p.nom }), message, confirmLabel: t("action.confirm"), danger: true });
    if (!ok) return;
    try { await deleteStoragePool(p.nom, p.node === "local" ? undefined : p.node, fsBacked); pushToast({ kind: "success", title: t("stor.removed"), message: p.nom }); refreshAll(); }
    catch (err) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(err) }); }
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
                        <td><Chip title={p.type === "zfs" ? t("stor.zfsLocal") : undefined}>{typeLabel(p.type)}{p.type === "netfs" && p.nfs_version ? ` v${p.nfs_version}` : ""}</Chip></td>
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
                          {p.node !== "local" ? <span className="nx-muted">{t("stor.volLocalOnly")}</span> : vols == null ? <span className="nx-muted">{t("loading")}</span> : vols.length === 0 ? <span className="nx-muted">{t("stor.noVolumes")}</span> : (
                            <ul className="nx-list nx-list--vols">{vols.map((v) => <li key={v.nom}><span className="nx-mono">{v.nom}</span><span className="nx-mono nx-muted">{formatSizeGb(v.capacite_go, lang)}</span><span className="nx-muted">{v.utilise ? t("stor.inUse") : t("stor.free")}</span></li>)}</ul>
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
        "-",
        { key: "delete", icon: "delete", label: t("vx.delete"), danger: true, run: () => removePool(p),
          disabled: !caps.admin || p.nom === "default", reason: !caps.admin ? t("menu.reason.admin") : t("ctx.defaultPool") },
      ]} />
    </>
  );
}
StoragePage.ownHeader = true;
