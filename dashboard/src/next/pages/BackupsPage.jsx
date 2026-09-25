import { useCallback, useEffect, useState } from "react";
import { fetchAllBackups, deleteBackup } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { ErrorState } from "../components/States";

const STATUS = { termine: "termine", echec: "echec" };

// Backups of every VM in one table. Failed backups can be removed here; restores stay in each VM's
// Backup tab (they need a target choice), reachable from the VM name.
export default function BackupsPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const pushToast = useInfraStore((s) => s.pushToast);
  const caps = capabilities(useAuthStore((s) => s.role));
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try { const r = await fetchAllBackups(); setRows(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const running = Boolean(rows?.some((b) => !STATUS[b.statut]));
  usePolling(load, 4000, { enabled: running });

  async function remove(b) {
    if (!(await confirmAction({ title: `Delete backup #${b.id}?`, message: t("bk.deleteHelp"), confirmLabel: "Delete", danger: true }))) return;
    try { await deleteBackup(b.id); pushToast({ kind: "success", title: t("bk.deleted"), message: `#${b.id}` }); load(); }
    catch (e) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(e) }); }
  }
  const fmt = (ts) => new Intl.DateTimeFormat(lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(ts));

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="bk-h">
        <div className="nx-cardhead"><h2 id="bk-h">{t("nav.backups")} <span className="nx-count">{rows ? rows.length : "…"}</span></h2><button type="button" className="nx-btn" onClick={load}>{t("top.refresh")}</button></div>
        {error ? <ErrorState message={error} onRetry={load} />
          : rows == null ? <p className="nx-muted">{t("loading")}</p>
          : rows.length === 0 ? <div className="nx-drawer-empty"><strong>{t("bk.none")}</strong><span className="nx-muted">{t("bk.noneHelp")}</span></div> : (
            <div className="nx-tablewrap">
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("jr.time")}</th><th scope="col">VM</th><th scope="col">{t("bk.mode")}</th><th scope="col" className="nx-num">{t("bk.size")}</th><th scope="col">{t("bk.location")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.id}>
                      <td><StatusIndicator kind="task" wire={b.statut === "termine" ? "termine" : b.statut === "echec" ? "echec" : "en_cours"} /></td>
                      <td className="nx-mono">{fmt(b.cree_le)}</td>
                      <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("vm", b.vm_name, "backup")}>{b.vm_name}</button></th>
                      <td>{b.mode === "chaud" ? t("bk.hot") : t("bk.cold")}</td>
                      <td className="nx-num nx-mono">{b.taille_octets ? formatSizeMb(b.taille_octets / 1048576, lang) : "—"}</td>
                      <td className="nx-mono" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{b.erreur ? <span className="nx-tone-danger">{b.erreur}</span> : b.chemin}</td>
                      <td className="nx-num">{caps.admin && b.statut === "echec" && <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete backup #${b.id}`} onClick={() => remove(b)}>{t("menu.delete").replace("…", "")}</button>}</td>
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
