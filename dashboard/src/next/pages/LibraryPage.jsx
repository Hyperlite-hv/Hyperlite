import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Copy, Disc3, Trash2, Upload } from "lucide-react";
import { fetchClusterIsos, deleteIso, fetchTemplates } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { formatSizeMb, formatDateTime } from "../lib/format";
import { errorMessage } from "../lib/errors";
import { PageHeader, Empty, Loading, TableWrap } from "../components/ui";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";
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
      setUnreachable(Array.isArray(r?.injoignables) ? r.injoignables : []);
    } catch (e) { pushToast({ kind: "error", title: t("stor.isoError"), message: errorMessage(e) }); setIsos([]); }
  }, [pushToast, t]);
  useEffect(() => { loadIsos(); }, [loadIsos]);
  useEffect(() => { fetchTemplates().then((r) => setTplCount(Array.isArray(r) ? r.length : 0)).catch(() => setTplCount(null)); }, [tab]);
  // A finished copy adds an image somewhere: reload when the number of running copies goes down.
  const runningCopies = tasks.filter((x) => x.type === "copy_iso" && x.statut === "en_cours").length;
  const prevRunning = useRef(runningCopies);
  useEffect(() => { if (runningCopies < prevRunning.current) loadIsos(); prevRunning.current = runningCopies; }, [runningCopies, loadIsos]);

  const nodeName = useCallback((id) => nodes.find((n) => n.id === id)?.nom || id, [nodes]);
  const holders = useMemo(() => {
    const m = new Map();
    for (const iso of isos || []) { if (!m.has(iso.nom)) m.set(iso.nom, new Set()); m.get(iso.nom).add(iso.node); }
    return m;
  }, [isos]);
  const rows = useMemo(() => [...(isos || [])].sort((a, b) => a.nom.localeCompare(b.nom) || (a.node === "local" ? -1 : b.node === "local" ? 1 : nodeName(a.node).localeCompare(nodeName(b.node)))), [isos, nodeName]);
  const multiNode = nodes.length > 1;

  async function removeIso(iso) {
    const where = nodeName(iso.node);
    if (!(await confirmAction({ title: t("lib.isoDeleteTitle", { name: iso.nom }), message: multiNode ? t("iso.deleteOnNode", { node: where }) : t("stor.isoConfirm"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteIso(iso.nom, iso.node); pushToast({ kind: "success", title: t("stor.isoDeleted"), message: multiNode ? `${iso.nom} · ${where}` : iso.nom }); loadIsos(); }
    catch (err) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(err) }); }
  }
  const tabs = [["iso", t("lib.iso"), holders.size || (isos ? 0 : null)], ["tpl", t("lib.templates"), tplCount]];
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
              <div className="nx-bn" data-tone="warning" role="status"><span className="nx-bn-t">{t("iso.unreachable", { nodes: unreachable.map((u) => `${nodeName(u.node)} (${u.erreur})`).join(", ") })}</span></div>
            )}
            <div className="nx-card2 nx-card2--flush">
              {isos == null ? <Loading style={{ padding: "var(--space-4)" }} /> : isos.length === 0 ? <Empty icon={Disc3} title={t("stor.noIso")} text={t("lib.isoNoneHelp")} /> : (
                <TableWrap>
                  <table className="nx-table">
                    <thead><tr><th scope="col">{t("lib.image")}</th>{multiNode && <th scope="col">{t("iso.node")}</th>}<th scope="col" className="nx-num">{t("lib.size")}</th>{!multiNode && <th scope="col">{t("lib.location")}</th>}<th scope="col">{t("lib.added")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                    <tbody>
                      {rows.map((iso) => (
                        <tr key={`${iso.node}/${iso.nom}`} className={ctx.is("iso", `${iso.node}/${iso.nom}`) ? "is-ctx" : undefined} onContextMenu={ctx.open("iso", iso, `${iso.node}/${iso.nom}`)}>
                          <th scope="row" className="nx-mono" style={{ fontWeight: 500 }}>{iso.nom}</th>
                          {multiNode && <td title={iso.emplacement || undefined}>{nodeName(iso.node)}</td>}
                          <td className="nx-num nx-mono">{formatSizeMb(iso.taille_mo, lang)}</td>
                          {!multiNode && <td className="nx-mono nx-muted">{iso.emplacement || "—"}</td>}
                          <td className="nx-mono nx-muted">{iso.ajoutee_le ? formatDateTime(iso.ajoutee_le, lang) : "—"}</td>
                          <td><div className="nx-ra">{caps.admin && (<>
                            {multiNode && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" aria-label={t("iso.copyX", { v: iso.nom, node: nodeName(iso.node) })} onClick={() => setCopying(iso)}><Copy size={15} aria-hidden="true" /><span className="nx-hide-narrow">{t("iso.copy")}</span></button>}
                            <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_iso_x", { v: multiNode ? `${iso.nom} (${nodeName(iso.node)})` : iso.nom })} title={t("vx.delete")} onClick={() => removeIso(iso)}><Trash2 size={15} aria-hidden="true" /></button>
                          </>)}</div></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </div>
            <ActionsContextMenu ctx={ctx} label={(iso) => t("ctx.menuOf", { name: iso.nom })} entries={(iso) => [
              multiNode && { key: "copy", icon: "clone", label: t("iso.copy"), run: () => setCopying(iso), disabled: !caps.admin, reason: t("menu.reason.admin") },
              { key: "name", icon: "copy", label: t("ctx.copyName"), run: () => navigator.clipboard?.writeText(iso.nom) },
              "-",
              { key: "delete", icon: "delete", label: t("vx.delete"), danger: true, run: () => removeIso(iso), disabled: !caps.admin, reason: t("menu.reason.admin") },
            ]} />
            {copying && <CopyIsoDialog iso={copying} nodes={nodes} holders={holders.get(copying.nom) || new Set()} onClose={() => setCopying(null)} />}
          </>
        ) : <TemplatesPanel />}
      </div>
    </>
  );
}
LibraryPage.ownHeader = true;
