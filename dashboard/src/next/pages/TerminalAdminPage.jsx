import { useCallback, useEffect, useState } from "react";
import { SquareTerminal } from "lucide-react";
import { endTerminalSession, fetchTerminalSessions, fetchTerminalSettings, saveTerminalSettings } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Field, Loading, PageHeader, TableWrap } from "../components/ui";

const NUMBERS = [["per_account", "ta.perAccount"], ["per_node", "ta.perNode"], ["idle_min", "ta.idle"], ["max_hours", "ta.maxHours"],
  ["memory_mb", "ta.memory"], ["cpu_percent", "ta.cpu"], ["tasks", "ta.tasks"], ["home_mb", "ta.home"]];

// The Terminal for administrators (docs/design/workbench.md, sections 12 and 13): who may use it, its limits, and the
// sessions open on this node, each ended from here.
export default function TerminalAdminPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [settings, setSettings] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { setSettings(await fetchTerminalSettings()); setSessions(await fetchTerminalSessions()); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  if (error && !settings) return <ErrorState message={error} onRetry={load} />;
  if (!settings) return <Loading />;
  async function save() {
    try { setSettings(await saveTerminalSettings(Object.fromEntries(Object.entries(settings).map(([k, v]) => [k, typeof v === "boolean" ? v : Number(v)])))); pushToast({ kind: "success", title: t("gr.saved") }); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  async function end(s) {
    if (!(await confirmAction({ title: t("tm.endTitle"), message: `${s.account} · ${s.id}`, confirmLabel: t("tm.end"), danger: true }))) return;
    try { await endTerminalSession(s.id); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  return (
    <>
      <PageHeader title={t("tab.terminal")} desc={t("ta.desc")} />
      <Card title={t("ta.sessions")} flush>
        {sessions.length === 0 ? <Empty icon={SquareTerminal} title={t("ta.none")} text="" /> : (
          <TableWrap label={t("ta.sessions")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("sec.user")}</th><th scope="col">{t("ta.since")}</th><th scope="col">{t("ta.from")}</th><th scope="col">{t("ac.status")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <th scope="row">{s.account}</th>
                    <td>{formatDateTime(s.opened_at, lang)}</td>
                    <td className="nx-mono">{s.from_address}</td>
                    <td><Chip tone={s.attached ? "success" : undefined}>{t(s.attached ? "ta.attached" : "ta.detached")}</Chip></td>
                    <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost" onClick={() => end(s)}>{t("tm.end")}</button></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <Card title={t("ta.settings")} note={t("ta.settingsNote")}>
        <label className="nx-check"><input type="checkbox" checked={settings.everyone} onChange={(e) => setSettings((x) => ({ ...x, everyone: e.target.checked }))} /> {t("ta.everyone")}</label>
        <div className="nx-fg">
          {NUMBERS.map(([k, l]) => <Field key={k} label={t(l)}>{(p) => <input {...p} type="number" className="nx-inp" value={settings[k]} onChange={(e) => setSettings((x) => ({ ...x, [k]: e.target.value }))} />}</Field>)}
        </div>
        <label className="nx-check"><input type="checkbox" checked={settings.recording} onChange={(e) => setSettings((x) => ({ ...x, recording: e.target.checked }))} /> {t("ta.recording")}</label>
        <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" onClick={save}>{t("gr.save")}</button></div>
      </Card>
    </>
  );
}
TerminalAdminPage.ownHeader = true;
