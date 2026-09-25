import { useCallback, useEffect, useState } from "react";
import { fetchVmExports, downloadVmExport, deleteVmExport } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import { ErrorState } from "../components/States";

// Disk exports produced from a VM ("Export the disk"): download with a one-time ticket, delete after a
// confirmation. Refreshes while an export may be running; a failure shows one message, not a stack of toasts.
export default function ExportsPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try { const r = await fetchVmExports(); setRows(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 8000);

  async function download(nom) { try { await downloadVmExport(nom); } catch (e) { pushToast({ kind: "error", title: t("ex.downloadFailed"), message: errorMessage(e) }); } }
  async function remove(nom) {
    if (!(await confirmAction({ title: t("ex.deleteTitle"), message: t("ex.deleteMsg", { name: nom }), confirmLabel: "Delete", danger: true }))) return;
    try { await deleteVmExport(nom); pushToast({ kind: "success", title: t("ex.deleted"), message: nom }); load(); }
    catch (e) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(e) }); }
  }
  const fmt = (ts) => new Intl.DateTimeFormat(lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(ts));

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="ex-h">
        <div className="nx-cardhead"><h2 id="ex-h">{t("nav.exports")} <span className="nx-count">{rows ? rows.length : "…"}</span></h2></div>
        {error ? <ErrorState message={error} onRetry={load} />
          : rows == null ? <p className="nx-muted">{t("loading")}</p>
          : rows.length === 0 ? <div className="nx-drawer-empty"><strong>{t("ex.none")}</strong><span className="nx-muted">{t("ex.noneHelp")}</span></div> : (
            <div className="nx-tablewrap">
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ex.file")}</th><th scope="col" className="nx-num">{t("bk.size")}</th><th scope="col">{t("ex.created")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.nom}>
                      <th scope="row" className="nx-mono" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{r.nom}</th>
                      <td className="nx-num nx-mono">{r.taille_octets ? formatSizeMb(r.taille_octets / 1048576, lang) : "—"}</td>
                      <td className="nx-mono">{fmt(r.modifie_le)}</td>
                      <td className="nx-num" style={{ display: "flex", gap: "var(--space-2)", justifyContent: "flex-end" }}>
                        <button type="button" className="nx-btn" aria-label={`Download export ${r.nom}`} onClick={() => download(r.nom)}>{t("ex.download")}</button>
                        <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete export ${r.nom}`} onClick={() => remove(r.nom)}>{t("menu.delete").replace("…", "")}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>
    </div>
  );
}
