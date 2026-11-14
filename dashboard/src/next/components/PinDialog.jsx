import { useEffect, useState } from "react";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { useDashboards } from "../lib/dashboards";
import { Field, SideDrawer } from "./ui";

// "Pin to a dashboard": the tile goes to one of the dashboards this account may change, or to a new one.
export function PinDialog({ open, onClose, tile }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const { list, load, pin } = useDashboards();
  const [choice, setChoice] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) load().then((l) => { const mine = (l || []).filter((d) => d.editable); setChoice(mine[0]?.id || "new"); }); }, [open, load]);
  const mine = (list || []).filter((d) => d.editable);
  const valid = choice && (choice !== "new" || name.trim());
  async function go() {
    setBusy(true);
    try {
      const d = await pin(tile, choice === "new" ? { newName: name.trim() } : { id: choice });
      pushToast({ kind: "success", title: t("pd.done"), message: d.name });
      setName(""); onClose();
    } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
    setBusy(false);
  }
  return (
    <SideDrawer open={open} title={t("pd.title")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!valid || busy} onClick={go}>{t("pd.action")}</button>
    </>}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("pd.what", { title: tile?.title || "" })}</p>
      <fieldset className="nx-fs">
        <legend>{t("pd.where")}</legend>
        {mine.map((d) => (
          <label key={d.id} className="hc-radio"><input type="radio" name="pin-dash" checked={choice === d.id} onChange={() => setChoice(d.id)} /> {d.name}{d.shared !== "private" && <small className="nx-muted"> · {t("dsh.sharedShort")}</small>}</label>
        ))}
        <label className="hc-radio"><input type="radio" name="pin-dash" checked={choice === "new"} onChange={() => setChoice("new")} /> {t("pd.new")}</label>
      </fieldset>
      {choice === "new" && <Field label={t("dsh.name")}>{(p) => <input {...p} className="nx-inp" value={name} maxLength={80} autoFocus onChange={(e) => setName(e.target.value)} placeholder={t("dsh.namePh")} />}</Field>}
    </SideDrawer>
  );
}
