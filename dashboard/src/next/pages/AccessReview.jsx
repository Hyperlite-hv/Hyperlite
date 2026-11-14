import { useCallback, useEffect, useState } from "react";
import { deleteAcl, fetchAccessReview, keepAssignment } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { Card, Chip, TableWrap } from "../components/ui";

const FLAG_TONE = { orphan: "danger", unknown_role: "danger", stale_account: "warning", ending_soon: "warning", gives_access: "accent", whole_cluster: "accent", to_review: undefined };

// The access review (docs/design/access-2.md, section 8): each assignment with what helps decide on it, kept (noted
// for 90 days) or removed from here; then the accounts and tokens that look forgotten.
export default function AccessReview({ roles, canDecide }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [data, setData] = useState(null);
  const [all, setAll] = useState(false);
  const [notes, setNotes] = useState({});

  const load = useCallback(() => fetchAccessReview().then(setData).catch(() => setData(null)), []);
  useEffect(() => { load(); }, [load]);
  if (!data) return null;

  async function keep(a) {
    try { await keepAssignment(a.id, (notes[a.id] || "").trim()); pushToast({ kind: "success", title: t("rv.kept") }); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  async function remove(a) {
    if (!(await confirmAction({ title: t("sec.aclDeleteTitle"), message: t("sec.aclDeleteMsg"), confirmLabel: t("sec.remove"), danger: true }))) return;
    try { await deleteAcl(a.id); pushToast({ kind: "success", title: t("rv.removed") }); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  const shown = data.assignments.filter((a) => all || a.flags.some((f) => f !== "gives_access" && f !== "whole_cluster"));
  const where = (a) => (a.resource_type === "cluster" ? t("acc.wholeCluster") : `${t(`acc.kind.${a.resource_type}`)} ${a.resource_label || a.resource_id}`);
  const when = (iso) => (iso ? formatDateTime(iso, lang) : t("rv.never"));
  const s = data.summary;

  return (
    <>
      <Card title={t("rv.title")} note={t("rv.note", { days: data.stale_days })} actions={<label className="nx-check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> {t("rv.showAll")}</label>} flush>
        <div className="hc-rv-sum">
          <Chip>{t("rv.sum.assignments", { n: s.assignments })}</Chip>
          <Chip tone={s.to_review ? "warning" : undefined}>{t("rv.sum.toReview", { n: s.to_review })}</Chip>
          <Chip tone={s.orphans ? "danger" : undefined}>{t("rv.sum.orphans", { n: s.orphans })}</Chip>
          <Chip>{t("rv.sum.ending", { n: s.ending_soon })}</Chip>
        </div>
        {shown.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("rv.allGood")}</p> : (
          <TableWrap label={t("rv.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.subject")}</th><th scope="col">{t("sec.role")}</th><th scope="col">{t("sec.on")}</th><th scope="col">{t("rv.lastLogin")}</th><th scope="col">{t("rv.flags")}</th>{canDecide && <th scope="col"><span className="nx-sr">{t("actions")}</span></th>}</tr></thead>
              <tbody>
                {shown.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">{a.subject_type === "group" ? `${t("sec.group")} ${a.subject_label}` : a.subject_label}</th>
                    <td>{roles[a.role]?.label || a.role}</td>
                    <td>{where(a)}</td>
                    <td>{a.subject_type === "user" ? when(a.last_login_at) : "—"}</td>
                    <td>
                      {a.flags.map((f) => <Chip key={f} tone={FLAG_TONE[f]}>{t(`rv.flag.${f}`)}</Chip>)}
                      {a.review && <small className="nx-muted"> {t("rv.keptBy", { by: a.review.reviewed_by, d: formatDateTime(a.review.reviewed_at, lang) })}{a.review.note ? ` — ${a.review.note}` : ""}</small>}
                    </td>
                    {canDecide && <td><div className="nx-ra hc-rv-act">
                      <input className="nx-inp" aria-label={t("rv.noteX", { who: a.subject_label })} placeholder={t("rv.notePh")} maxLength={500} value={notes[a.id] || ""} onChange={(e) => setNotes((n) => ({ ...n, [a.id]: e.target.value }))} />
                      <button type="button" className="nx-btn nx-btn--ghost" onClick={() => keep(a)}>{t("rv.keep")}</button>
                      <button type="button" className="nx-btn nx-btn--ghost" onClick={() => remove(a)}>{t("sec.remove")}</button>
                    </div></td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {(data.stale_accounts.length > 0 || data.admins_without_2fa.length > 0 || data.tokens.length > 0) && (
        <Card title={t("rv.forgotten")} note={t("rv.forgottenNote", { days: data.stale_days })}>
          {data.admins_without_2fa.length > 0 && <p><Chip tone="danger">{t("rv.no2fa")}</Chip> {data.admins_without_2fa.join(", ")}</p>}
          {data.stale_accounts.length > 0 && <p><Chip tone="warning">{t("rv.staleAccounts")}</Chip> {data.stale_accounts.map((u) => `${u.username} (${when(u.last_login_at)})`).join(", ")}</p>}
          {data.tokens.length > 0 && <p><Chip tone="warning">{t("rv.tokens")}</Chip> {data.tokens.map((k) => `${k.username}/${k.name}${k.expires_at ? "" : ` · ${t("rv.noExpiry")}`}`).join(", ")}</p>}
        </Card>
      )}
    </>
  );
}
