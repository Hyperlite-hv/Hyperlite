import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import { useT } from "../i18n";

// Building blocks of the console pages: the commands of a page, the filters of a list, the "Essentials" block and
// the property sections of a resource. Colours come from the tokens; the layout is in console.css.

// A command of the bar under a page title: an icon in the brand colour and a word.
export function Cmd({ icon: Icon, label, onClick, disabled, danger, title, menu, ...rest }) {
  return (
    <button type="button" className={`nx-btn${danger ? " nx-btn--danger" : ""}`} onClick={disabled ? undefined : onClick}
      aria-disabled={disabled || undefined} title={title} {...rest}>
      {Icon && <Icon size={16} aria-hidden="true" />}{label}{menu && <ChevronDown size={14} aria-hidden="true" className="hc-cmd-chev" />}
    </button>
  );
}
export const CmdSep = () => <span className="hc-cmd-sep" aria-hidden="true" />;

// A filter of a list, read as a sentence: "State equals: all". The value is a native select.
export function FilterPill({ label, value, options, onChange }) {
  const current = options.find((o) => o.value === value) || options[0];
  return (
    <label className={`hc-pill${value ? " is-set" : ""}`}>
      <span>{label}</span><b>{current?.label}</b>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown size={14} aria-hidden="true" />
    </label>
  );
}

// A choice shown like a command (grouping, list or cards).
export function PickCmd({ icon: Icon, label, value, options, onChange }) {
  const current = options.find((o) => o.value === value) || options[0];
  return (
    <label className="hc-pick">
      {Icon && <Icon size={16} aria-hidden="true" />}<span>{current?.label}</span><ChevronDown size={14} aria-hidden="true" />
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

// Label and value pairs on two columns; rows whose value is null are left out.
export function KeyValues({ rows }) {
  return (
    <dl className="hc-kv">
      {rows.filter((r) => r && r[1] != null && r[1] !== false).map(([k, v]) => (
        <div key={typeof k === "string" ? k : undefined}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

// The "Essentials" block of a resource: the facts that identify it, on two columns, foldable, with the raw
// object on demand.
export function Essentials({ left, right, footer, json, storageKey = "hyperlite-console-essentials" }) {
  const t = useT();
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(storageKey) !== "0"; } catch { return true; } });
  const [raw, setRaw] = useState(false);
  const toggle = () => setOpen((o) => { try { localStorage.setItem(storageKey, o ? "0" : "1"); } catch { /* preference only */ } return !o; });
  return (
    <section className="hc-ess" aria-label={t("ess.title")}>
      <div className="hc-ess-h">
        <button type="button" onClick={toggle} aria-expanded={open}>{open ? <ChevronUp size={16} aria-hidden="true" /> : <ChevronDown size={16} aria-hidden="true" />}{t("ess.title")}</button>
        {json && <button type="button" className="nx-lnk" onClick={() => setRaw(true)}>{t("ess.json")}</button>}
      </div>
      {open && <div className="hc-ess-g"><KeyValues rows={left} /><KeyValues rows={right} /></div>}
      {open && footer && <div className="hc-ess-f">{footer}</div>}
      {raw && <JsonPane title={t("ess.json")} value={json} onClose={() => setRaw(false)} />}
    </section>
  );
}

// A side pane showing an object as JSON, read-only.
export function JsonPane({ title, value, onClose }) {
  const t = useT();
  const ref = useRef(null);
  useEffect(() => {
    const back = document.activeElement;
    ref.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); back?.focus?.(); };
  }, [onClose]);
  return (
    <>
      <div className="hc-scrim" onClick={onClose} />
      <aside ref={ref} className="hc-pane" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}>
        <div className="hc-pane-h"><h2>{title}</h2><button type="button" className="hc-ibtn" aria-label={t("action.close")} onClick={onClose}><X size={18} aria-hidden="true" /></button></div>
        <div className="hc-pane-b"><pre className="hc-json">{JSON.stringify(value, null, 2)}</pre></div>
      </aside>
    </>
  );
}

// A titled group of properties, as on the overview of a resource: an icon, a title, an optional action, then rows.
export function PropSection({ icon: Icon, title, action, rows, children }) {
  return (
    <section className="hc-prop">
      <h2>{Icon && <Icon size={16} aria-hidden="true" />}{title}{action}</h2>
      {rows ? <KeyValues rows={rows} /> : children}
    </section>
  );
}
export const PropGrid = ({ children }) => <div className="hc-props">{children}</div>;

// Tabs inside a page (Properties, Monitoring...).
export function PageTabs({ tabs, value, onChange, label }) {
  return (
    <div className="nx-tabs hc-ptabs" role="tablist" aria-label={label}>
      {tabs.map(([id, text, n]) => (
        <button key={id} type="button" role="tab" aria-selected={value === id} tabIndex={value === id ? 0 : -1} onClick={() => onChange(id)}>
          {text}{n != null && <span className="nx-n">{n}</span>}
        </button>
      ))}
    </div>
  );
}

// Downloads rows as a CSV file (first row: the headers), quoting every cell.
export function downloadCsv(filename, rows) {
  const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const blob = new Blob([`\uFEFF${rows.map((r) => r.map(cell).join(";")).join("\r\n")}`], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
