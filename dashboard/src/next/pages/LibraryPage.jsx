import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Copy, Disc3, HardDrive, Trash2, Upload } from "lucide-react";
import { fetchClusterIsos, deleteIso, fetchTemplates, fetchVmDisks, deleteVmDisk, renameIso } from "../../api/client";
import { askNewName } from "../lib/rename";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { formatSizeMb, formatDateTime } from "../lib/format";
import { errorMessage } from "../lib/errors";
import { PageHeader, Empty, Loading, TableWrap } from "../components/ui";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";
import VmDiskUploadDropzone from "../../components/VmDiskUploadDropzone";
import { ErrorState } from "../components/States";
import TemplatesPanel from "./TemplatesPage";
import CopyIsoDialog from "../components/CopyIsoDialog";
import { ActionsContextMenu, useContextTarget } from "../components/ContextMenu";

// Library: the ISO images (moved from Storage) and the templates, in two tabs. The historical ?tab=templates
// link opens the Templates tab. The ISO list covers every node of the cluster: a VM boots only from an image on
// its own host, so an image is shared by copying it to other nodes' libraries.
export default function LibraryPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const activeTab = useInfraStore((s) => s.activeTab);
  const [tab, setTab] = useState(activeTab === "templates" ? "tpl" : "iso");
  const [isos, setIsos] = useState(null);
  const [unreachable, setUnreachable] = useState([]);
  const [copying, setCopying] = useState(null);
  const [tplCount, setTplCount] = useState(null);
  const nodes = useInfraStore((s) => s.nodes);
  const tasks = useInfraStore((s) => s.tasks);
  const drop = useRef(null);
  const ctx = useContextTarget(); // right click on an image: its actions

  const loadIsos = useCallback(async () => {
    try {
      const r = await fetchClusterIsos();
      setIsos(Array.isArray(r?.isos) ? r.isos : []);
      setUnreachable(Array.isArray(r?.unreachable) ? r.unreachable : []);
    } catch (e) { pushToast({ kind: "error", title: t("stor.isoError"), message: errorMessage(e) }); setIsos([]); }
  }, [pushToast, t]);
  useEffect(() => { loadIsos(); }, [loadIsos]);
  useEffect(() => { fetchTemplates().then((r) => setTplCount(Array.isArray(r) ? r.length : 0)).catch(() => setTplCount(null)); }, [tab]);
  // A finished copy adds an image somewhere: reload when the number of running copies goes down.
  const runningCopies = tasks.filter((x) => x.type === "copy_iso" && x.status === "running").length;
  const prevRunning = useRef(runningCopies);
  useEffect(() => { if (runningCopies < prevRunning.current) loadIsos(); prevRunning.current = runningCopies; }, [runningCopies, loadIsos]);

  const nodeName = useCallback((id) => nodes.find((n) => n.id === id)?.name || id, [nodes]);
  const holders = useMemo(() => {
    const m = new Map();
    for (const iso of isos || []) { if (!m.has(iso.name)) m.set(iso.name, new Set()); m.get(iso.name).add(iso.node); }
    return m;
  }, [isos]);
  const rows = useMemo(() => [...(isos || [])].sort((a, b) => a.name.localeCompare(b.name) || (a.node === "local" ? -1 : b.node === "local" ? 1 : nodeName(a.node).localeCompare(nodeName(b.node)))), [isos, nodeName]);
  const multiNode = nodes.length > 1;

  async function renameIsoFile(iso) {
    const name = await askNewName(t, { title: t("rn.isoTitle", { name: iso.name }), message: t("rn.isoMsg"), current: iso.name, rule: /^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}(\.iso)?$/i, ruleText: t("rn.isoRule") });
    if (!name) return;
    try { const r = await renameIso(iso.name, name); pushToast({ kind: "success", title: t("rn.done"), message: `${iso.name} → ${r?.name || name}` }); loadIsos(); }
    catch (e) { pushToast({ kind: "error", title: t("rn.failed"), message: errorMessage(e) }); }
  }
  async function removeIso(iso) {
    const where = nodeName(iso.node);
    if (!(await confirmAction({ title: t("lib.isoDeleteTitle", { name: iso.name }), message: multiNode ? t("iso.deleteOnNode", { node: where }) : t("stor.isoConfirm"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteIso(iso.name, iso.node); pushToast({ kind: "success", title: t("stor.isoDeleted"), message: multiNode ? `${iso.name} · ${where}` : iso.name }); loadIsos(); }
    catch (err) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(err) }); }
  }
  const tabs = [["iso", t("lib.iso"), holders.size || (isos ? 0 : null)], ["tpl", t("lib.templates"), tplCount], ...(caps.admin ? [["disks", t("lib.disks"), null]] : [])];
  const upload = () => drop.current?.querySelector("input[type=file]")?.click();

  return (
    <>
      <PageHeader title={t("tab.library")}
        actions={tab === "iso" && caps.admin && <button type="button" className="nx-btn nx-btn--primary" onClick={upload}><Upload size={15} aria-hidden="true" />{t("lib.upload")}</button>} />
      <div className="nx-tabs nx-tabs--page" role="tablist" aria-label={t("tab.library")}>
        {tabs.map(([id, label, n]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} id={`lib-${id}`} aria-controls="lib-panel" onClick={() => setTab(id)}>{label}{n != null && <span className="nx-n">{n}</span>}</button>
        ))}
      </div>
      <div id="lib-panel" role="tabpanel" aria-labelledby={`lib-${tab}`} className="nx-stack">
        {tab === "iso" ? (
          <>
            {caps.admin && <div ref={drop}><IsoUploadDropzone onDone={loadIsos} labels={{ drop: t("up.dropIso"), done: t("up.done"), eta: t("up.eta"), input: t("a11y.iso_file") }} /></div>}
            {unreachable.length > 0 && (
              <div className="nx-bn" data-tone="warning" role="status"><span className="nx-bn-t">{t("iso.unreachable", { nodes: unreachable.map((u) => `${nodeName(u.node)} (${u.error})`).join(", ") })}</span></div>
            )}
            <div className="nx-card2 nx-card2--flush">
              {isos == null ? <Loading style={{ padding: "var(--space-4)" }} /> : isos.length === 0 ? <Empty icon={Disc3} title={t("stor.noIso")} text={t("lib.isoNoneHelp")} /> : (
                <TableWrap>
                  <table className="nx-table">
                    <thead><tr><th scope="col">{t("lib.image")}</th>{multiNode && <th scope="col">{t("iso.node")}</th>}<th scope="col" className="nx-num">{t("lib.size")}</th>{!multiNode && <th scope="col">{t("lib.location")}</th>}<th scope="col">{t("lib.added")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                    <tbody>
                      {rows.map((iso) => (
                        <tr key={`${iso.node}/${iso.name}`} className={ctx.is("iso", `${iso.node}/${iso.name}`) ? "is-ctx" : undefined} onContextMenu={ctx.open("iso", iso, `${iso.node}/${iso.name}`)}>
                          <th scope="row" className="nx-mono" style={{ fontWeight: 500 }}>{iso.name}</th>
                          {multiNode && <td title={iso.emplacement || undefined}>{nodeName(iso.node)}</td>}
                          <td className="nx-num nx-mono">{formatSizeMb(iso.size_mb, lang)}</td>
                          {!multiNode && <td className="nx-mono nx-muted">{iso.emplacement || "—"}</td>}
                          <td className="nx-mono nx-muted">{iso.added_at ? formatDateTime(iso.added_at, lang) : "—"}</td>
                          <td><div className="nx-ra">{caps.admin && (<>
                            {multiNode && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("iso.copyX", { v: iso.name, node: nodeName(iso.node) })} onClick={() => setCopying(iso)}><Copy size={15} aria-hidden="true" /><span className="nx-hide-narrow">{t("iso.copy")}</span></button>}
                            <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_iso_x", { v: multiNode ? `${iso.name} (${nodeName(iso.node)})` : iso.name })} title={t("vx.delete")} onClick={() => removeIso(iso)}><Trash2 size={15} aria-hidden="true" /></button>
                          </>)}</div></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </div>
            <ActionsContextMenu ctx={ctx} label={(iso) => t("ctx.menuOf", { name: iso.name })} entries={(iso) => [
              multiNode && { key: "copy", icon: "clone", label: t("iso.copy"), run: () => setCopying(iso), disabled: !caps.admin, reason: t("menu.reason.admin") },
              { key: "name", icon: "copy", label: t("ctx.copyName"), run: () => navigator.clipboard?.writeText(iso.name) },
              "-",
              { key: "rename", icon: "rename", label: t("vx.renameMenu"), run: () => renameIsoFile(iso), disabled: !caps.admin || iso.node !== "local", reason: !caps.admin ? t("menu.reason.admin") : t("rn.isoLocal") },
              { key: "delete", icon: "delete", label: t("vx.delete"), danger: true, run: () => removeIso(iso), disabled: !caps.admin, reason: t("menu.reason.admin") },
            ]} />
            {copying && <CopyIsoDialog iso={copying} nodes={nodes} holders={holders.get(copying.name) || new Set()} onClose={() => setCopying(null)} />}
          </>
        ) : tab === "disks" ? <ImportedDisksPanel /> : <TemplatesPanel />}
      </div>
    </>
  );
}
LibraryPage.ownHeader = true;

// Disk images uploaded to create a VM from ("import a VM from a disk"): they were only reachable from the creation
// wizard, so they piled up with no screen to see or remove them. Administrators only, like the endpoints.
function ImportedDisksPanel() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [disks, setDisks] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { const r = await fetchVmDisks(); setDisks(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  async function remove(d) {
    if (!(await confirmAction({ title: t("lib.diskDeleteTitle", { name: d.name }), message: t("lib.diskDeleteMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteVmDisk(d.name); pushToast({ kind: "success", title: t("lib.diskDeleted"), message: d.name }); load(); }
    catch (e) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(e) }); }
  }
  return (
    <>
      <p className="nx-f-h" style={{ margin: 0 }}>{t("lib.disksHelp")}</p>
      <VmDiskUploadDropzone onDone={load} labels={{ drop: t("up.dropDisk"), done: t("up.done"), eta: t("up.eta") }} />
      <div className="nx-card2 nx-card2--flush">
        {error ? <ErrorState message={error} onRetry={load} />
          : disks == null ? <Loading style={{ padding: "var(--space-4)" }} />
          : disks.length === 0 ? <Empty icon={HardDrive} title={t("lib.noDisks")} text={t("lib.noDisksHelp")} /> : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("lib.image")}</th><th scope="col" className="nx-num">{t("lib.size")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {disks.map((d) => (
                    <tr key={d.name}>
                      <th scope="row" className="nx-mono" style={{ fontWeight: 500 }}>{d.name}</th>
                      <td className="nx-num nx-mono">{formatSizeMb(d.size_mb, lang)}</td>
                      <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("lib.diskDeleteAria", { name: d.name })} title={t("vx.delete")} onClick={() => remove(d)}><Trash2 size={15} aria-hidden="true" /></button></div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
      </div>
    </>
  );
}
