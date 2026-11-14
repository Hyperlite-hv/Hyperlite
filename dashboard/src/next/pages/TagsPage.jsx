import { useCallback, useEffect, useState } from "react";
import { Tags } from "lucide-react";
import { fetchTagSummary, retag } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { promptText } from "../../store/usePromptStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { useAccess } from "../lib/access";
import { useMetaStore } from "../lib/meta";
import { useQueryStore } from "../lib/query";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Loading, PageHeader, TableWrap } from "../components/ui";

// Every tag in use, with how many VMs, containers, nodes, pools, storage pools and networks carry it; a tag is renamed,
// merged into another or removed everywhere at once, and opens the objects that carry it in the Search page.
export default function TagsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const admin = !!useAccess((s) => s.me?.admin);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => fetchTagSummary().then((r) => { setRows(r); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  async function change(tag, next, title) {
    try {
      const r = await retag(tag, next);
      pushToast({ kind: "success", title, message: t("tg.changed", { n: r.changed }) });
      useMetaStore.getState().load?.(true);
      load();
    } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  async function rename(tag) {
    const next = (await promptText({ title: t("tg.renameTitle", { tag }), label: t("tg.newName"), defaultValue: tag }))?.trim().toLowerCase();
    if (!next || next === tag) return;
    const merging = rows.some((r) => r.tag === next);
    if (merging && !(await confirmAction({ title: t("tg.mergeTitle"), message: t("tg.mergeMsg", { tag, next }), confirmLabel: t("tg.merge") }))) return;
    change(tag, next, merging ? t("tg.merged") : t("tg.renamed"));
  }
  async function remove(tag, total) {
    if (!(await confirmAction({ title: t("tg.removeTitle", { tag }), message: t("tg.removeMsg", { n: total }), confirmLabel: t("sec.remove"), danger: true }))) return;
    change(tag, null, t("tg.removed"));
  }
  if (error && !rows) return <ErrorState message={error} onRetry={load} />;
  if (!rows) return <Loading />;
  return (
    <>
      <PageHeader title={t("tab.tags")} count={rows.length} desc={t("tg.desc")} />
      <Card flush>
        {rows.length === 0 ? <Empty icon={Tags} title={t("tg.none")} text={t("tg.noneText")} /> : (
          <TableWrap label={t("tab.tags")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("tg.tag")}</th><th scope="col" className="nx-num">{t("tg.objects")}</th><th scope="col">{t("tg.kinds")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.tag}>
                    <th scope="row"><button type="button" className="nx-lnk" onClick={() => { useQueryStore.getState().setQ(`tag:${r.tag}`); navigateTo("datacenter", null, "search"); }}><span className="nx-tag">{r.tag}</span></button></th>
                    <td className="nx-num nx-mono">{r.total}</td>
                    <td>{Object.entries(r.kinds).map(([k, n]) => <Chip key={k}>{t(`acc.kind.${k}`)} · {n}</Chip>)}</td>
                    <td><div className="nx-ra">{admin && <>
                      <button type="button" className="nx-btn nx-btn--ghost" onClick={() => rename(r.tag)}>{t("tg.rename")}</button>
                      <button type="button" className="nx-btn nx-btn--ghost" onClick={() => remove(r.tag, r.total)}>{t("sec.remove")}</button>
                    </>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
    </>
  );
}
TagsPage.ownHeader = true;
