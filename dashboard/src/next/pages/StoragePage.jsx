import { Fragment, useCallback, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchIsoTemplates, deleteIso, createStoragePool, deleteStoragePool, fetchVolumes } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { formatSizeGb, formatSizeMb } from "../lib/format";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";

const EMPTY = { name: "", type: "dir", node: "local", path: "", nfs_host: "", nfs_export_path: "", size_gb: "20" };
const levelOf = (r) => (r >= 0.9 ? "danger" : r >= 0.8 ? "warning" : "accent");

// Storage: pools (usage with thresholds, volumes on demand, create / remove) and the ISO library.
// Same API calls, payloads and safeguards as the historical screen (the default pool is never removable;
// removing a directory/NFS pool only removes its definition, never its files).
export default function StoragePage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { pools, nodes, refreshAll, pushToast } = useInfraStore(useShallow((s) => ({ pools: s.storagePools, nodes: s.nodes, refreshAll: s.refreshAll, pushToast: s.pushToast })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const [isos, setIsos] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [formOpen, setFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(null);
  const [volumes, setVolumes] = useState({});

  const loadIsos = useCallback(async () => {
    try { const r = await fetchIsoTemplates(); setIsos(Array.isArray(r) ? r : []); }
    catch (e) { pushToast({ kind: "error", title: t("stor.isoError"), message: errorMessage(e) }); setIsos([]); }
  }, [pushToast, t]);
  useEffect(() => { loadIsos(); }, [loadIsos]);

  async function toggleVolumes(p) {
    const key = `${p.node}:${p.nom}`;
    setOpen(open === key ? null : key);
    if (open !== key && p.node === "local" && !volumes[key]) {
      try { const v = await fetchVolumes(p.nom); setVolumes((x) => ({ ...x, [key]: Array.isArray(v) ? v : [] })); }
      catch { setVolumes((x) => ({ ...x, [key]: [] })); }
    }
  }

  async function create(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = form.type === "dir" ? { name: form.name, type: "dir", path: form.path || null }
        : form.type === "netfs" ? { name: form.name, type: "netfs", nfs_host: form.nfs_host, nfs_export_path: form.nfs_export_path }
        : { name: form.name, type: "zfs", size_gb: Number(form.size_gb) };
      // "local" is the frontend sentinel of the local host: the backend only accepts registered remote nodes.
      await createStoragePool(payload, form.node === "local" ? undefined : form.node);
      pushToast({ kind: "success", title: t("stor.created"), message: form.name });
      setForm(EMPTY); setFormOpen(false); refreshAll();
    } catch (err) { pushToast({ kind: "error", title: t("stor.createFailed"), message: errorMessage(err) }); }
    finally { setBusy(false); }
  }

  async function removePool(p) {
    if (p.nom === "default") return;
    const fsBacked = p.type === "dir" || p.type === "netfs";
    const ok = await confirmAction({
      title: t("stor.confirmTitle", { name: p.nom }),
      message: fsBacked ? t("stor.confirmFs", { name: p.nom }) : t("stor.confirmEmpty", { name: p.nom }),
      confirmLabel: "Confirm", danger: true,
    });
    if (!ok) return;
    try {
      await deleteStoragePool(p.nom, p.node === "local" ? undefined : p.node, fsBacked);
      pushToast({ kind: "success", title: t("stor.removed"), message: p.nom }); refreshAll();
    } catch (err) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(err) }); }
  }

  async function removeIso(nom) {
    if (!(await confirmAction({ title: `Delete ISO '${nom}'?`, message: t("stor.isoConfirm"), confirmLabel: "Delete" }))) return;
    try { await deleteIso(nom); pushToast({ kind: "success", title: t("stor.isoDeleted"), message: nom }); loadIsos(); }
    catch (err) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(err) }); }
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const TYPES = [["dir", t("stor.type.dir")], ["netfs", t("stor.type.netfs")], ["zfs", "ZFS"]];

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="stor-pools">
        <div className="nx-cardhead">
          <h2 id="stor-pools">{t("inv.storage")} <span className="nx-count">{pools.length}</span></h2>
          {caps.admin && <button type="button" className="nx-btn" aria-expanded={formOpen} onClick={() => setFormOpen((o) => !o)}>{t("stor.createPool")}</button>}
        </div>

        {formOpen && (
          <form className="nx-form" onSubmit={create}>
            <div className="nx-formgrid">
              <label>{t("stor.poolName")}<input className="nx-input" aria-label="Pool name" required value={form.name} onChange={set("name")} placeholder="nfs-shared" /></label>
              <label>{t("ns.node")}<select className="nx-input" aria-label="Node" value={form.node} onChange={set("node")}>{nodes.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}</select></label>
            </div>
            <div className="nx-seg nx-seg--wide" role="group" aria-label={t("stor.poolType")}>
              {TYPES.map(([v, label]) => <button key={v} type="button" aria-pressed={form.type === v} onClick={() => setForm((f) => ({ ...f, type: v }))}>{label}</button>)}
            </div>
            {form.type === "dir" && <label>{t("stor.path")}<input className="nx-input" aria-label="Local path (optional)" value={form.path} onChange={set("path")} placeholder="/var/lib/libvirt/hyperlite-pools/…" /><span className="nx-hint">{t("stor.pathHelp")}</span></label>}
            {form.type === "netfs" && (
              <div className="nx-formgrid">
                <label>{t("stor.nfsHost")}<input className="nx-input" aria-label="NFS server host" required value={form.nfs_host} onChange={set("nfs_host")} placeholder="192.168.1.10" /></label>
                <label>{t("stor.nfsPath")}<input className="nx-input" aria-label="Exported path" required value={form.nfs_export_path} onChange={set("nfs_export_path")} placeholder="/srv/share" /></label>
              </div>
            )}
            {form.type === "zfs" && <label>{t("stor.zfsSize")}<input className="nx-input" aria-label="Size (GB, loopback file)" type="number" min="1" max="4096" required value={form.size_gb} onChange={set("size_gb")} /><span className="nx-hint">{t("stor.zfsHelp")}</span></label>}
            <div className="nx-formactions">
              <button type="button" className="nx-btn" onClick={() => setFormOpen(false)}>{t("action.cancel")}</button>
              <button type="submit" className="nx-btn nx-btn--primary" disabled={busy}>{busy ? t("stor.creating") : t("action.create")}</button>
            </div>
          </form>
        )}

        {pools.length === 0 ? <p className="nx-muted" role="status">{t("ov.noPools")}</p> : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("stor.pool")}</th><th scope="col">{t("ns.node")}</th><th scope="col">{t("stor.type")}</th><th scope="col">{t("stor.usage")}</th><th scope="col" className="nx-num">{t("stor.capacity")}</th><th scope="col" className="nx-num">{t("stor.free")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {pools.map((p) => {
                  const key = `${p.node}:${p.nom}`;
                  const r = p.capacite_go ? (p.capacite_go - (p.disponible_go ?? p.capacite_go)) / p.capacite_go : null;
                  const vols = volumes[key];
                  return (
                    <Fragment key={key}>
                      <tr>
                        <td><StatusIndicator kind="pool" wire={p.etat} /></td>
                        <th scope="row"><button type="button" className="nx-link" aria-expanded={open === key} onClick={() => toggleVolumes(p)}>{p.nom}</button></th>
                        <td className="nx-mono">{nodes.find((n) => n.id === p.node)?.nom || p.node}</td>
                        <td>{p.type === "netfs" ? "NFS" : p.type === "zfs" ? "ZFS" : p.type}{p.type === "zfs" && <span className="nx-muted" title={t("stor.zfsLocal")}> ⓘ</span>}</td>
                        <td>{r == null ? <span className="nx-muted">{t("ns.notReported")}</span> : <span className="nx-mono">{Math.round(r * 100)} %<span className="nx-progress nx-progress--inline" role="meter" aria-label={`${p.nom} ${t("stor.usage")}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(r * 100)}><span style={{ width: `${Math.round(r * 100)}%`, background: `var(--color-${levelOf(r)})` }} /></span></span>}</td>
                        <td className="nx-num nx-mono">{formatSizeGb(p.capacite_go, lang) ?? "—"}</td><td className="nx-num nx-mono">{formatSizeGb(p.disponible_go, lang) ?? "—"}</td>
                        <td className="nx-num">{caps.admin && p.nom !== "default" && <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete pool ${p.nom}`} onClick={() => removePool(p)}>{t("menu.delete").replace("…", "")}</button>}</td>
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
          </div>
        )}
      </section>

      <section className="nx-card" aria-labelledby="stor-iso">
        <div className="nx-cardhead"><h2 id="stor-iso">{t("stor.iso")} <span className="nx-count">{isos ? isos.length : "…"}</span></h2></div>
        {caps.admin && <IsoUploadDropzone onDone={loadIsos} />}
        {isos && isos.length === 0 ? <p className="nx-muted" role="status" style={{ marginTop: "var(--space-3)" }}>{t("stor.noIso")}</p> : (
          <ul className="nx-list nx-list--vols" style={{ marginTop: "var(--space-4)" }}>
            {(isos || []).map((iso) => (
              <li key={iso.nom}><span className="nx-mono">{iso.nom}</span><span className="nx-mono nx-muted">{formatSizeMb(iso.taille_mo, lang)}</span>
                {caps.admin ? <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete ISO ${iso.nom}`} onClick={() => removeIso(iso.nom)}>{t("menu.delete").replace("…", "")}</button> : <span />}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
