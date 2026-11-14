import { useState } from "react";
import { Copy, Download } from "lucide-react";
import { fetchVmDefinition } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Card } from "./ui";

const FORMATS = [["create", "def.create", "json"], ["json", "def.json", "json"], ["terraform", "def.terraform", "tf"]];

// A VM's definition, to make it again elsewhere or keep it in a repository: a body for POST /vms, the full JSON, or
// the Terraform / OpenTofu resource (app/core/vm_definition.py).
export default function DefinitionCard({ vm }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [format, setFormat] = useState("terraform");
  const [text, setText] = useState(null);
  async function show(f) {
    setFormat(f);
    try {
      const d = await fetchVmDefinition(vm.name, f);
      setText(typeof d === "string" ? d : JSON.stringify(d, null, 2));
    } catch (e) { pushToast({ kind: "error", title: t("def.failed"), message: errorMessage(e) }); }
  }
  function download() {
    const ext = FORMATS.find(([f]) => f === format)[2];
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `${vm.name}.${ext}` });
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <Card title={t("def.title")} note={t("def.note")}>
      <div className="hc-seg" role="group" aria-label={t("def.title")}>
        {FORMATS.map(([f, label]) => <button key={f} type="button" className="nx-btn nx-btn--ghost" aria-pressed={text !== null && format === f} onClick={() => show(f)}>{t(label)}</button>)}
      </div>
      {text !== null && (
        <>
          <pre className="hc-def nx-mono" tabIndex={0}>{text}</pre>
          <div className="nx-fa">
            <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigator.clipboard?.writeText(text).then(() => pushToast({ kind: "success", title: t("def.copied") }))}><Copy size={14} aria-hidden="true" /> {t("def.copy")}</button>
            <button type="button" className="nx-btn" onClick={download}><Download size={14} aria-hidden="true" /> {t("def.download")}</button>
          </div>
        </>
      )}
    </Card>
  );
}
