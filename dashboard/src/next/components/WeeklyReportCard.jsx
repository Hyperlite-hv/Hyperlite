import { useEffect, useState } from "react";
import { Send } from "lucide-react";
import { fetchWeeklyReport, saveWeeklyReportSettings, sendWeeklyReport } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { InlineError } from "./States";

const DAYS = [0, 1, 2, 3, 4, 5, 6];

// Notifications ▸ Weekly report: when it goes out, to the channels taking the "Weekly report" event, and what it
// would say now.
export default function WeeklyReportCard() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(false);
  const load = () => fetchWeeklyReport().then((r) => { setData(r); setError(null); }).catch((e) => setError(errorMessage(e)));
  useEffect(() => { load(); }, []);
  async function save(patch) {
    const next = { ...data.settings, ...patch };
    setData((d) => ({ ...d, settings: next }));
    try { await saveWeeklyReportSettings(next); } catch (e) { pushToast({ kind: "error", title: t("wr.saveFailed"), message: errorMessage(e) }); load(); }
  }
  async function sendNow() {
    setBusy(true);
    try { await sendWeeklyReport(); pushToast({ kind: "success", title: t("wr.sent") }); load(); }
    catch (e) { pushToast({ kind: "error", title: t("wr.sendFailed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  if (error) return <section className="hc-wr"><h2>{t("wr.title")}</h2><InlineError message={error} onRetry={load} /></section>;
  if (!data) return null;
  const s = data.settings;
  return (
    <section className="hc-wr" aria-labelledby="hc-wr-h">
      <h2 id="hc-wr-h">{t("wr.title")}</h2>
      <p className="nx-muted">{t("wr.desc")}</p>
      <div className="hc-wr-row">
        <label className="nx-check"><input type="checkbox" checked={s.enabled} onChange={(e) => save({ enabled: e.target.checked })} />{t("wr.enabled")}</label>
        <label className="hc-mx-f"><span>{t("wr.day")}</span><select className="nx-inp" value={s.weekday} disabled={!s.enabled} onChange={(e) => save({ weekday: Number(e.target.value) })}>{DAYS.map((d) => <option key={d} value={d}>{t(`wr.d.${d}`)}</option>)}</select></label>
        <label className="hc-mx-f"><span>{t("wr.hour")}</span><select className="nx-inp" value={s.hour} disabled={!s.enabled} onChange={(e) => save({ hour: Number(e.target.value) })}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{`${String(h).padStart(2, "0")}:00`}</option>)}</select></label>
        <button type="button" className="nx-btn" onClick={sendNow} disabled={busy}><Send size={15} aria-hidden="true" />{busy ? t("wr.sending") : t("wr.sendNow")}</button>
        <button type="button" className="nx-lnk" aria-expanded={preview} onClick={() => setPreview(!preview)}>{preview ? t("wr.hidePreview") : t("wr.preview")}</button>
      </div>
      <p className="nx-hint">{data.last_sent ? t("wr.lastSent", { date: formatDateTime(data.last_sent, lang) }) : t("wr.neverSent")} {t("wr.channelsHint")}</p>
      {preview && <pre className="hc-run-pre" tabIndex={0}>{data.text}</pre>}
    </section>
  );
}
