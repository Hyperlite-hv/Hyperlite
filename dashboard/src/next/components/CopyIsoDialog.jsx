import { useEffect, useRef, useState } from "react";
import { copyIso } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";

// Share one ISO image with other nodes: tick the nodes that do not have it yet, and each copy runs as its own
// task. Nodes that already hold it are listed as such, offline ones cannot be picked; the server checks again.
export default function CopyIsoDialog({ iso, nodes, holders, onClose, onStarted }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const candidates = nodes.filter((n) => n.id !== iso.node);
  const pickable = candidates.filter((n) => !holders.has(n.id) && n.etat === "online");
  const [picked, setPicked] = useState(() => new Set(pickable.length === 1 ? [pickable[0].id] : []));
  const [busy, setBusy] = useState(false);
  const first = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);

  useEffect(() => { first.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);

  const toggle = (id) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const allPicked = pickable.length > 0 && pickable.every((n) => picked.has(n.id));
  const nameOf = (id) => nodes.find((n) => n.id === id)?.nom || id;

  async function start() {
    setBusy(true);
    try {
      const targets = [...picked];
      await copyIso(iso.nom, iso.node, targets);
      pushToast({ kind: "success", title: t("iso.copyStarted"), message: t("iso.copyStartedMsg", { name: iso.nom, nodes: targets.map(nameOf).join(", ") }) });
      onStarted?.();
      onClose();
    } catch (e) {
      pushToast({ kind: "error", title: t("iso.copyFailed"), message: errorMessage(e) });
      setBusy(false);
    }
  }

  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="nx-dialog" role="dialog" aria-modal="true" aria-labelledby="isocp-title" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
        <h2 id="isocp-title">{t("iso.copyTitle", { name: iso.nom })}</h2>
        <p className="nx-muted" style={{ margin: 0 }}>{t("iso.copyHelp", { node: nameOf(iso.node) })}</p>
        {candidates.length === 0 ? <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("iso.noOtherNode")}</p> : (
          <fieldset className="nx-dialog-field" style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: "var(--space-2)" }}>
            <legend className="nx-sr">{t("iso.copyTargets")}</legend>
            {pickable.length > 1 && (
              <label className="nx-check"><input ref={first} type="checkbox" checked={allPicked} disabled={busy}
                onChange={(e) => setPicked(new Set(e.target.checked ? pickable.map((n) => n.id) : []))} /> <b>{t("iso.allNodes")}</b></label>
            )}
            {candidates.map((n, i) => {
              const has = holders.has(n.id);
              const offline = n.etat !== "online";
              return (
                <label key={n.id} className="nx-check">
                  <input ref={pickable.length <= 1 && i === 0 ? first : undefined} type="checkbox" checked={picked.has(n.id)} disabled={busy || has || offline} onChange={() => toggle(n.id)} />
                  {" "}{n.nom}
                  {has && <span className="nx-muted"> · {t("iso.alreadyThere")}</span>}
                  {!has && offline && <span className="nx-muted"> · {t("iso.nodeOffline")}</span>}
                </label>
              );
            })}
          </fieldset>
        )}
        <div className="nx-dialog-actions">
          <button type="button" className="nx-btn" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" disabled={picked.size === 0 || busy} onClick={start}>{busy ? t("iso.copying") : t("iso.copyGo", { n: picked.size })}</button>
        </div>
      </div>
    </div>
  );
}
