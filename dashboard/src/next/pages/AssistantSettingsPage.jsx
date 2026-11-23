import { useEffect, useState } from "react";
import { MessageCircleQuestion } from "lucide-react";
import { fetchAssistantSettings, saveAssistantSettings } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Card, Field, Loading, PageHeader } from "../components/ui";

// The assistant's settings (docs/design/console-2.md, section 10): off by default; where questions go, and what leaves
// the node, said plainly.
export default function AssistantSettingsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [s, setS] = useState(null);
  const [key, setKey] = useState("");
  useEffect(() => { fetchAssistantSettings().then(setS).catch((e) => pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) })); }, [pushToast, t]);
  if (!s) return <><PageHeader title={t("tab.assistant")} desc={t("as2.desc")} /><Loading /></>;
  const set = (k) => (e) => setS((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  async function save() {
    const body = { enabled: s.enabled, base_url: s.base_url, model: s.model, log_text: s.log_text, ...(key ? { api_key: key } : {}) };
    try { setS(await saveAssistantSettings(body)); setKey(""); pushToast({ kind: "success", title: t("as2.saved") }); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  return (
    <>
      <PageHeader title={t("tab.assistant")} desc={t("as2.desc")} actions={<button type="button" className="nx-btn nx-btn--primary" onClick={save}>{t("as2.save")}</button>} />
      <div className="nx-bn" data-tone="info"><MessageCircleQuestion size={16} aria-hidden="true" /><span className="nx-bn-t">{t("as2.privacyLocal")}</span></div>
      <Card>
        <label className="nx-check"><input type="checkbox" checked={s.enabled} onChange={set("enabled")} /> {t("as2.enabled")}</label>
        <Field label={t("as2.baseUrl")} hint={t("as2.baseUrlHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={s.base_url} onChange={set("base_url")} />}</Field>
        <Field label={t("as2.model")} hint={t("as2.modelHintO")}>{(p) => <input {...p} className="nx-inp nx-mono" value={s.model} onChange={set("model")} placeholder="llama3.1" />}</Field>
        <Field label={t("as2.key")} hint={s.has_key ? t("as2.keySet") : t("as2.keyOptional")}>{(p) => <input {...p} type="password" autoComplete="off" className="nx-inp nx-mono" value={key} onChange={(e) => setKey(e.target.value)} />}</Field>
        <label className="nx-check"><input type="checkbox" checked={s.log_text} onChange={set("log_text")} /> {t("as2.logText")}</label>
      </Card>
    </>
  );
}
AssistantSettingsPage.ownHeader = true;
