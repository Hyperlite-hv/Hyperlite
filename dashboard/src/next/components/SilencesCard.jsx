import { useCallback, useEffect, useState } from "react";
import { BellOff } from "lucide-react";
import { addSilence, fetchPools, fetchSilences, removeSilence } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { Card, Chip, Field, SideDrawer, TableWrap } from "./ui";

const local = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

// Silences of the alert rules: during a maintenance window, a node, a VM, a pool's VMs, a rule or everything still
// fires (the journal says so) but notifies nobody.
export default function SilencesCard({ rules, canEdit }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const nodes = useInfraStore((s) => s.nodes);
  const vms = useInfraStore((s) => s.vms);
  const [rows, setRows] = useState(null);
  const [pools, setPools] = useState([]);
  const [adding, setAdding] = useState(null);
  const [now, setNow] = useState(0); // when the list was read: "running" is judged against it
  const load = useCallback(() => fetchSilences().then((r) => { setRows(r); setNow(Date.now()); }).catch(() => setRows([])), []);
  useEffect(() => { load(); fetchPools().then(setPools).catch(() => {}); }, [load]);
  if (!rows) return null;
  const label = (s) => s.target_kind === "all" ? t("sl.all") : s.target_kind === "rule" ? `${t("sl.rule")} ${rules.find((r) => r.id === s.target)?.name || s.target}`
    : s.target_kind === "pool" ? `${t("acc.kind.pool")} ${pools.find((p) => String(p.id) === s.target)?.name || s.target}` : `${t(`acc.kind.${s.target_kind}`)} ${s.target}`;
  const options = { node: nodes.map((n) => [n.id === "local" ? n.name : n.id, n.name]), vm: vms.map((v) => [v.name, v.name]), pool: pools.map((p) => [String(p.id), p.name]), rule: rules.map((r) => [r.id, r.name]) };
  async function submit() {
    try {
      await addSilence({ target_kind: adding.kind, target: adding.kind === "all" ? null : adding.target, starts_at: new Date(adding.start).toISOString(), ends_at: new Date(adding.end).toISOString(), reason: adding.reason });
      setAdding(null); load(); pushToast({ kind: "success", title: t("sl.added") });
    } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("sl.title")} note={t("sl.note")} flush actions={canEdit && <button type="button" className="nx-btn" onClick={() => { const d = new Date(); setAdding({ kind: "all", target: "", start: local(d), end: local(new Date(d.getTime() + 2 * 3600e3)), reason: "" }); }}><BellOff size={14} aria-hidden="true" /> {t("sl.add")}</button>}>
      {rows.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("sl.none")}</p> : (
        <TableWrap label={t("sl.title")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("sl.on")}</th><th scope="col">{t("sl.when")}</th><th scope="col">{t("gr.reason")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {rows.map((s) => {
                const active = new Date(s.starts_at).getTime() <= now && now < new Date(s.ends_at).getTime();
                return (
                  <tr key={s.id}>
                    <th scope="row">{label(s)} {active && <Chip tone="warning">{t("sl.active")}</Chip>}</th>
                    <td>{formatDateTime(s.starts_at, lang)} → {formatDateTime(s.ends_at, lang)}</td>
                    <td>{s.reason || "—"}</td>
                    <td><div className="nx-ra">{canEdit && <button type="button" className="nx-btn nx-btn--ghost" onClick={async () => { await removeSilence(s.id).catch(() => {}); load(); }}>{t("sec.remove")}</button>}</div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableWrap>
      )}
      {adding && (
        <SideDrawer open title={t("sl.add")} onClose={() => setAdding(null)} footer={<>
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setAdding(null)}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" disabled={adding.kind !== "all" && !adding.target} onClick={submit}>{t("sl.add")}</button>
        </>}>
          <div className="nx-fg">
            <Field label={t("sl.on")}>{(p) => <select {...p} className="nx-inp" value={adding.kind} onChange={(e) => setAdding((x) => ({ ...x, kind: e.target.value, target: "" }))}>{["all", "node", "vm", "pool", "rule"].map((k) => <option key={k} value={k}>{k === "all" ? t("sl.all") : k === "rule" ? t("sl.rule") : t(`acc.kind.${k}`)}</option>)}</select>}</Field>
            {adding.kind !== "all" && <Field label={t("sec.resource")}>{(p) => <select {...p} className="nx-inp" value={adding.target} onChange={(e) => setAdding((x) => ({ ...x, target: e.target.value }))}><option value="">{t("sec.choose")}</option>{options[adding.kind].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>}
          </div>
          <div className="nx-fg">
            <Field label={t("gu.start")}>{(p) => <input {...p} type="datetime-local" className="nx-inp" value={adding.start} onChange={(e) => setAdding((x) => ({ ...x, start: e.target.value }))} />}</Field>
            <Field label={t("gu.end")}>{(p) => <input {...p} type="datetime-local" className="nx-inp" value={adding.end} onChange={(e) => setAdding((x) => ({ ...x, end: e.target.value }))} />}</Field>
          </div>
          <Field label={t("gr.reason")}>{(p) => <input {...p} className="nx-inp" maxLength={500} value={adding.reason} onChange={(e) => setAdding((x) => ({ ...x, reason: e.target.value }))} placeholder={t("sl.reasonPh")} />}</Field>
        </SideDrawer>
      )}
    </Card>
  );
}
