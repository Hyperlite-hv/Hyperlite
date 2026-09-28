import { useCallback, useEffect, useState } from "react";
import { fetchVmExports, downloadVmExport, deleteVmExport } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { ActionsContextMenu, useContextTarget } from "../components/ContextMenu";
import { usePolling } from "../lib/polling";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import { ErrorState } from "../components/States";
import { PageHeader, Empty, Loading, TableWrap } from "../components/ui";
import { Download, Share, Trash2 } from "lucide-react";

// Disk exports produced from a VM ("Export the disk"): download with a one-time ticket, delete after a
// confirmation. Refreshes while an export may be running; a failure shows one message, not a stack of toasts.
export default function ExportsPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const ctx = useContextTarget(); // right click on an export: its actions
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try { const r = await fetchVmExports(); setRows(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 8000);

  async function download(nom) { try { await downloadVmExport(nom); } catch (e) { pushToast({ kind: "error", title: t("ex.downloadFailed"), message: errorMessage(e) }); } }
  async function remove(nom) {
    if (!(await confirmAction({ title: t("ex.deleteTitle"), message: t("ex.deleteMsg", { name: nom }), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteVmExport(nom); pushToast({ kind: "success", title: t("ex.deleted"), message: nom }); load(); }
    catch (e) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(e) }); }
  }
  const fmt = (ts) => new Intl.DateTimeFormat(lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(ts));

  return (
    <>
      <PageHeader title={t("tab.exports")} count={rows ? rows.length : null} desc={t("ex.desc")} />
      <div className="nx-card2 nx-card2--flush">
        {error ? <ErrorState message={error} onRetry={load} />
          : rows == null ? <Loading style={{ padding: "var(--space-4)" }} />
          : rows.length === 0 ? <Empty icon={Share} title={t("ex.none")} text={t("ex.noneHelp")} /> : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ex.file")}</th><th scope="col" className="nx-num">{t("bk.size")}</th><th scope="col">{t("ex.created")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.nom} className={ctx.is("ex", r.nom) ? "is-ctx" : undefined} onContextMenu={ctx.open("ex", r)}>
                      <th scope="row" className="nx-mono nx-wrapcell" style={{ fontWeight: 500 }}>{r.nom}</th>
                      <td className="nx-num nx-mono">{r.taille_octets ? formatSizeMb(r.taille_octets / 1048576, lang) : "—"}</td>
                      <td className="nx-mono">{fmt(r.modifie_le)}</td>
                      <td><div className="nx-ra">
                        <button type="button" className="nx-btn nx-btn--sm" aria-label={t("a11y.download_export_x", { v: r.nom })} onClick={() => download(r.nom)}><Download size={14} aria-hidden="true" />{t("ex.download")}</button>
                        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("a11y.delete_export_x", { v: r.nom })} title={t("vx.delete")} onClick={() => remove(r.nom)}><Trash2 size={15} aria-hidden="true" /></button>
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
      </div>
      <ActionsContextMenu ctx={ctx} label={(r) => t("ctx.menuOf", { name: r.nom })} entries={(r) => [
        { key: "download", icon: "download", label: t("ex.download"), run: () => download(r.nom) },
        { key: "name", icon: "copy", label: t("ctx.copyName"), run: () => navigator.clipboard?.writeText(r.nom) },
        "-",
        { key: "delete", icon: "delete", label: t("vx.delete"), danger: true, run: () => remove(r.nom) },
      ]} />
    </>
  );
}
ExportsPage.ownHeader = true;
