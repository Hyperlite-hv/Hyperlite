import { useCallback, useEffect, useState } from "react";
import { RotateCcw, Trash2 } from "lucide-react";
import { fetchRecycleBin, purgeFromBin, restoreFromBin, setRecycleBinDays } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatSizeMb } from "../lib/format";
import { useAccess } from "../lib/access";
import { refreshInventory } from "../lib/inventory";
import { ErrorState } from "../components/States";
import { Card, Empty, Field, Loading, PageHeader, TableWrap } from "../components/ui";

// The recycle bin (docs/design/governance.md, section 7): deleted VMs kept some days, their disks in place, brought
// back as they were or deleted for good.
export default function RecycleBinPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const admin = !!useAccess((s) => s.me?.admin);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [days, setDays] = useState("");
  const load = useCallback(() => fetchRecycleBin().then((d) => { setData(d); setDays(String(d.days)); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  async function act(fn, title) {
    try { await fn(); pushToast({ kind: "success", title }); await load(); refreshInventory().catch(() => {}); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;
  return (
    <>
      <PageHeader title={t("tab.bin")} count={data.items.length} desc={data.days > 0 ? t("bin.desc", { n: data.days }) : t("bin.off")} />
      <Card flush>
        {data.items.length === 0 ? <Empty icon={Trash2} title={t("bin.empty")} text={t("bin.emptyText")} /> : (
          <TableWrap label={t("tab.bin")}>
            <table className="nx-table">
              <thead><tr><th scope="col">VM</th><th scope="col">{t("bin.size")}</th><th scope="col">{t("bin.deleted")}</th><th scope="col">{t("bin.until")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.vm_name}>
                    <th scope="row" className="nx-mono">{i.vm_name}<br /><small className="nx-muted">{i.disks.join(", ")}</small></th>
                    <td className="nx-mono">{i.vcpu} vCPU · {formatSizeMb(i.memory_mb, lang)}</td>
                    <td>{formatDateTime(i.deleted_at, lang)} · {i.deleted_by}</td>
                    <td>{formatDateTime(i.purge_at, lang)}</td>
                    <td><div className="nx-ra">
                      <button type="button" className="nx-btn" onClick={() => act(() => restoreFromBin(i.vm_name), t("bin.restored"))}><RotateCcw size={14} aria-hidden="true" /> {t("bin.restore")}</button>
                      <button type="button" className="nx-btn nx-btn--ghost" onClick={async () => { if (await confirmAction({ title: t("bin.purgeTitle"), message: t("bin.purgeMsg", { vm: i.vm_name }), confirmLabel: t("bin.purge"), danger: true })) act(() => purgeFromBin(i.vm_name), t("bin.purged")); }}><Trash2 size={14} aria-hidden="true" /> {t("bin.purge")}</button>
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {admin && (
        <Card title={t("bin.settings")}>
          <div className="nx-fg">
            <Field label={t("bin.days")} hint={t("bin.daysHint")}>{(p) => <input {...p} type="number" min="0" max="365" className="nx-inp" value={days} onChange={(e) => setDays(e.target.value)} />}</Field>
          </div>
          <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" onClick={() => act(() => setRecycleBinDays(Number(days)), t("gr.saved"))}>{t("gr.save")}</button></div>
        </Card>
      )}
    </>
  );
}
RecycleBinPage.ownHeader = true;
