import { useEffect, useState } from "react";
import { Braces, Copy, Download } from "lucide-react";
import { fetchContainer, fetchVM } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { SideDrawer } from "./ui";

// A resource as the API gives it (for a script, a ticket, a quick look at every field): fetched fresh, shown as
// indented JSON, copied or downloaded.
export function JsonViewButton({ type, resource }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="hc-ibtn hc-ph-json" aria-label={t("json.open")} title={t("json.open")} onClick={() => setOpen(true)}><Braces size={18} aria-hidden="true" /></button>
      {open && <JsonDrawer type={type} resource={resource} onClose={() => setOpen(false)} />}
    </>
  );
}

function JsonDrawer({ type, resource, onClose }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [text, setText] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let live = true;
    const load = type === "vm" ? fetchVM(resource.name, resource.node) : type === "container" ? fetchContainer(resource.name) : Promise.resolve(resource);
    load.then((obj) => { if (live) setText(JSON.stringify(obj, null, 2)); }).catch((e) => { if (live) setError(errorMessage(e)); });
    return () => { live = false; };
  }, [type, resource]);
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    Object.assign(document.createElement("a"), { href: url, download: `${resource.name}.json` }).click();
    URL.revokeObjectURL(url);
  }
  return (
    <SideDrawer open title={t("json.title", { name: resource.name })} onClose={onClose} footer={text && <>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigator.clipboard?.writeText(text).then(() => pushToast({ kind: "success", title: t("def.copied") }))}><Copy size={14} aria-hidden="true" /> {t("def.copy")}</button>
      <button type="button" className="nx-btn" onClick={download}><Download size={14} aria-hidden="true" /> {t("def.download")}</button>
    </>}>
      {error && <p className="nx-err" role="alert">{error}</p>}
      {text === null && !error ? <p className="nx-muted">{t("nn.connecting")}</p> : <pre className="hc-def nx-mono hc-json" tabIndex={0}>{text}</pre>}
    </SideDrawer>
  );
}
