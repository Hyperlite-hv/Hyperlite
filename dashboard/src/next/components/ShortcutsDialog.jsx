import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { useT } from "../i18n";
import { serviceLabel } from "../lib/services";
import { GO } from "../lib/shortcuts";

const Keys = ({ keys }) => <span className="hc-keys">{keys.map((k, i) => <kbd key={i}>{k}</kbd>)}</span>;

// "?": every shortcut of the console, by what it does.
export default function ShortcutsDialog({ open, onClose }) {
  const t = useT();
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const back = document.activeElement;
    ref.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); if (back && document.contains(back)) back.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  const general = [
    [["Ctrl", "K"], t("kbd.palette")], [["/"], t("kbd.search")], [["?"], t("kbd.help")], [["c"], t("kbd.create")],
    [["Ctrl", "B"], t("kbd.sidebar")], [["Esc"], t("kbd.close")],
  ];
  const lists = [[["j"], t("kbd.next")], [["k"], t("kbd.prev")], [[t("kbd.enter")], t("kbd.open")], [["x"], t("kbd.tick")]];
  return (
    <div className="nx-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="hc-keys-dlg" role="dialog" aria-modal="true" aria-labelledby="hc-keys-h" tabIndex={-1} ref={ref}>
        <div className="hc-keys-h"><h2 id="hc-keys-h">{t("kbd.title")}</h2><button type="button" className="hc-ibtn" aria-label={t("action.close")} onClick={onClose}><X size={16} aria-hidden="true" /></button></div>
        <div className="hc-keys-cols">
          <section><h3>{t("kbd.general")}</h3><dl>{general.map(([k, what]) => <div key={what}><dt><Keys keys={k} /></dt><dd>{what}</dd></div>)}</dl>
            <h3>{t("kbd.lists")}</h3><dl>{lists.map(([k, what]) => <div key={what}><dt><Keys keys={k} /></dt><dd>{what}</dd></div>)}</dl></section>
          <section><h3>{t("kbd.goTo")}</h3><dl>{Object.entries(GO).map(([key, id]) => <div key={key}><dt><Keys keys={["g", key]} /></dt><dd>{id === "summary" ? t("nav.overview") : t(serviceLabel(id))}</dd></div>)}</dl></section>
        </div>
      </div>
    </div>
  );
}
