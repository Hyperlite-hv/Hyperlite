import { useEffect, useRef } from "react";
import { Archive, BellRing, CheckCheck, CircleAlert, Info, Pin, TriangleAlert, X } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { taskLabel } from "../lib/enums";
import { relativeTime } from "../lib/format";
import { parseLink, useFeed } from "../lib/feed";
import { useLangStore } from "../i18n";

// The notifications pane opened from the header: the operations running now, with their progress, then the open
// alerts, each leading to the object it concerns.
export default function NotificationsPanel({ open, onClose, ops, alerts, returnFocusRef }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    requestAnimationFrame(() => ref.current?.focus());
    const onKey = (e) => {
      if (e.key === "Escape" && !e.defaultPrevented) { onClose(); return; }
      if (e.key !== "Tab" || !ref.current) return;
      // A modal pane keeps the focus: Tab past its last control comes back to its first, and the other way round.
      const items = [...ref.current.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])")].filter((el) => !el.disabled);
      if (!items.length) return;
      const first = items[0]; const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKey);
    const back = returnFocusRef?.current;
    return () => { window.removeEventListener("keydown", onKey); if (back && document.body.contains(back)) back.focus(); };
  }, [open, onClose, returnFocusRef]);

  const { items, unread, markRead, archive } = useFeed();
  const lang = useLangStore((st) => st.lang);
  if (!open) return null;
  const go = (type, id, tab) => { onClose(); navigateTo(type, id, tab || "summary"); };
  const openEntry = (e) => {
    if (!e.read) markRead([e.id]);
    const l = parseLink(e.link);
    if (l) go(l.type, l.id, l.tab);
  };
  const LEVEL_ICON = { error: CircleAlert, warning: TriangleAlert, update: BellRing, info: Info };
  // The entry worded in the reader's language from its kind; the server's English sentence for a kind not known here.
  const titleOf = (e) => { const k = `nf.${e.kind}`; const v = t(k, { subject: e.subject || "" }); return v === k ? e.title : v; };

  return (
    <>
      <div className="hc-scrim" onClick={onClose} />
      <aside ref={ref} className="hc-pane" role="dialog" aria-modal="true" aria-label={t("notif.title")} tabIndex={-1}>
        <div className="hc-pane-h">
          <h2>{t("notif.title")}</h2>
          <button type="button" className="hc-ibtn" aria-label={t("action.close")} onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </div>
        <div className="hc-pane-b">
          <div className="hc-feed-h">
            <h3>{t("nf.title", { n: unread })}</h3>
            {unread > 0 && <button type="button" className="nx-lnk" onClick={() => markRead(null)}><CheckCheck size={14} aria-hidden="true" />{t("nf.readAll")}</button>}
          </div>
          {items.length === 0 ? <p className="nx-muted">{t("nf.none")}</p> : (
            <ul className="hc-feed">
              {items.slice(0, 30).map((e) => {
                const Icon = LEVEL_ICON[e.level] || Info;
                return (
                  <li key={e.id} data-level={e.level} data-unread={!e.read || undefined} data-pinned={e.pinned || undefined}>
                    <span className="hc-feed-ico" aria-hidden="true"><Icon size={16} /></span>
                    <button type="button" className="hc-feed-main" onClick={() => openEntry(e)}>
                      <b>{titleOf(e)}</b>
                      {e.detail && <small>{e.detail}</small>}
                      <small className="hc-feed-meta">{e.pinned && <><Pin size={11} aria-hidden="true" />{t("nf.pinned")} · </>}{relativeTime(Date.parse(e.created_at), lang)}{!e.read && <span className="nx-sr"> · {t("nf.unread")}</span>}</small>
                    </button>
                    {!e.pinned && <button type="button" className="hc-ibtn" aria-label={t("nf.archive", { title: titleOf(e) })} title={t("nf.archiveShort")} onClick={() => archive([e.id])}><Archive size={14} aria-hidden="true" /></button>}
                  </li>
                );
              })}
            </ul>
          )}
          <h3>{t("notif.ops", { n: ops.length })}</h3>
          {ops.length === 0 ? <p className="nx-muted">{t("top.opsNone")}</p> : (
            <ul className="hc-nlist">
              {ops.map((o) => {
                const p = typeof o.progress === "number" && o.progress > 0 ? Math.min(100, Math.round(o.progress)) : null;
                return (
                  <li key={o.id}>
                    <div><b>{taskLabel(o.type)}</b>{o.target ? <> · {o.target}</> : null}{o.node ? <span className="nx-muted"> · {o.node}</span> : null}{p != null && <span className="hc-nlist-p">{p} %</span>}</div>
                    <span className={`nx-tray-bar${p == null ? " is-indet" : ""}`} role="progressbar" aria-label={taskLabel(o.type)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={p ?? undefined}>
                      <span style={p == null ? undefined : { width: `${p}%` }} />
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <h3>{t("notif.alerts", { n: alerts.length })}</h3>
          {alerts.length === 0 ? <p className="nx-muted">{t("ns.noIncident")}</p> : (
            <ul className="hc-nlist">
              {alerts.map((a) => (
                <li key={a.id} data-tone={a.level === "warning" ? "warning" : "danger"}>
                  <div><b>{a.text}</b><small>{t(`alert.kind.${a.kind}`)}{a.detail ? ` — ${a.detail}` : ""}</small></div>
                  {a.target && <button type="button" className="nx-lnk" onClick={() => go(a.target.type, a.target.id, a.target.tab)}>{t("dock.view")}</button>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="hc-pane-f">
          <button type="button" className="nx-lnk" onClick={() => go("datacenter", null, "activity")}>{t("notif.openActivity")}</button>
        </div>
      </aside>
    </>
  );
}
