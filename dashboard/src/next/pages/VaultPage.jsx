import { useCallback, useEffect, useState } from "react";
import { Eye, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { deleteSecret, fetchAccessSubjects, fetchVault, revealSecret, saveSecret } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { useAccess } from "../lib/access";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

// The secrets vault (docs/design/governance.md, section 8): values sealed by the node, revealed one at a time to the
// accounts given access (journaled), and named instead of copied (`vault:<name>` as a VM's password).
export default function VaultPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const admin = !!useAccess((s) => s.me?.admin);
  const [rows, setRows] = useState(null);
  const [subjects, setSubjects] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [shown, setShown] = useState({});
  const load = useCallback(async () => {
    try { setRows(await fetchVault()); setSubjects(await fetchAccessSubjects().catch(() => null)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  async function reveal(name) {
    try { const r = await revealSecret(name); setShown((x) => ({ ...x, [name]: r.value })); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  if (error && !rows) return <ErrorState message={error} onRetry={load} />;
  if (!rows) return <Loading />;
  const reader = (g) => (g.subject_type === "group" ? `${t("sec.group")} ${subjects?.groups.find((x) => String(x.id) === g.subject_id)?.name || g.subject_id}` : g.subject_id);
  return (
    <>
      <PageHeader title={t("tab.vault")} count={rows.length} desc={t("vt.desc")} actions={admin && <button type="button" className="nx-btn nx-btn--primary" onClick={() => setEditing({ name: "", value: "", description: "", readers: [] })}><Plus size={14} aria-hidden="true" /> {t("vt.new")}</button>} />
      <Card flush>
        {rows.length === 0 ? <Empty icon={KeyRound} title={t("vt.none")} text={t("vt.noneText")} /> : (
          <TableWrap label={t("tab.vault")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("pl.name")}</th><th scope="col">{t("pl.description")}</th><th scope="col">{t("vt.readers")}</th><th scope="col">{t("vt.changed")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id}>
                    <th scope="row" className="nx-mono">{s.name}<br /><small className="nx-muted">vault:{s.name}</small></th>
                    <td>{s.description || "—"}</td>
                    <td>{s.readers.length ? s.readers.map((g) => <Chip key={g.id}>{reader(g)}</Chip>) : <span className="nx-muted">{t("vt.adminsOnly")}</span>}</td>
                    <td>{formatDateTime(s.updated_at, lang)}</td>
                    <td><div className="nx-ra">
                      {shown[s.name] !== undefined ? <code className="nx-mono hc-secret">{shown[s.name]}</code> : <button type="button" className="nx-btn nx-btn--ghost" onClick={() => reveal(s.name)}><Eye size={14} aria-hidden="true" /> {t("vt.reveal")}</button>}
                      {admin && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("gr.edit")} onClick={() => setEditing({ ...s, value: "", readers: s.readers.map((g) => `${g.subject_type}:${g.subject_id}`) })}><Pencil size={14} aria-hidden="true" /></button>}
                      {admin && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("sec.remove")} onClick={async () => { if (await confirmAction({ title: t("vt.deleteTitle"), message: s.name, confirmLabel: t("sec.remove"), danger: true })) { await deleteSecret(s.id); load(); } }}><Trash2 size={14} aria-hidden="true" /></button>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {editing && <SecretDrawer t={t} s={editing} subjects={subjects} onClose={() => setEditing(null)} onDone={() => { setEditing(null); load(); pushToast({ kind: "success", title: t("gr.saved") }); }} />}
    </>
  );
}
VaultPage.ownHeader = true;

function SecretDrawer({ t, s, subjects, onClose, onDone }) {
  const [f, setF] = useState(s);
  const [error, setError] = useState(null);
  const options = [...(subjects?.users || []).map((u) => [`user:${u.username}`, u.username]), ...(subjects?.groups || []).map((g) => [`group:${g.id}`, `${t("sec.group")} ${g.name}`])];
  const toggle = (k) => setF((x) => ({ ...x, readers: x.readers.includes(k) ? x.readers.filter((y) => y !== k) : [...x.readers, k] }));
  async function submit() {
    try { await saveSecret({ name: f.name.trim(), value: f.value || null, description: f.description, readers: f.readers }, s.id || null); onDone(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <SideDrawer open title={s.id ? s.name : t("vt.new")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim() || (!s.id && !f.value)} onClick={submit}>{t("gr.save")}</button>
    </>}>
      <Field label={t("pl.name")}>{(p) => <input {...p} className="nx-inp nx-mono" maxLength={64} value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} />}</Field>
      <Field label={t("vt.value")} hint={s.id ? t("vt.valueKeep") : null}>{(p) => <textarea {...p} className="nx-inp nx-mono" rows={3} autoComplete="off" value={f.value} onChange={(e) => setF((x) => ({ ...x, value: e.target.value }))} />}</Field>
      <Field label={t("pl.description")}>{(p) => <input {...p} className="nx-inp" maxLength={500} value={f.description} onChange={(e) => setF((x) => ({ ...x, description: e.target.value }))} />}</Field>
      <fieldset className="nx-fs"><legend>{t("vt.readers")}</legend>
        <div className="nx-checks">{options.map(([k, l]) => <label key={k} className="nx-check"><input type="checkbox" checked={f.readers.includes(k)} onChange={() => toggle(k)} /> {l}</label>)}</div>
        <p className="nx-muted">{t("vt.readersHint")}</p>
      </fieldset>
      {error && <p className="nx-err" role="alert">{error}</p>}
    </SideDrawer>
  );
}
