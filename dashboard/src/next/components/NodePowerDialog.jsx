import { useEffect, useRef, useState } from "react";
import { fetchHealth, nodePower } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";

// Reboot or shut down this node. The host name has to be typed back (the same check is made by the server), and
// what runs on the node is either moved first (maintenance mode) or shut down cleanly: the node goes down only
// when every VM and container stopped.
function NodePowerDialog({ node, action, onClose }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const vms = useInfraStore((s) => s.vms);
  const [hostname, setHostname] = useState(null);
  const [typed, setTyped] = useState("");
  const [stopGuests, setStopGuests] = useState(false);
  const [busy, setBusy] = useState(false);
  const first = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  useEffect(() => { first.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);
  useEffect(() => { fetchHealth().then((h) => setHostname(h.hostname || node.nom)).catch(() => setHostname(node.nom)); }, [node.nom]);
  const running = vms.filter((v) => v.node === node.id && v.etat === "actif").map((v) => v.nom);

  async function go() {
    setBusy(true);
    try {
      await nodePower({ action, confirmation: typed.trim(), arreter_invites: stopGuests });
      pushToast({ kind: "success", title: t(`np.${action}.started`), message: t(stopGuests ? "np.afterGuests" : "np.soon") });
      onClose();
      navigateTo("node", node.id, "tasks");
    } catch (e) {
      pushToast({ kind: "error", title: t("np.failed"), message: errorMessage(e) });
      setBusy(false);
    }
  }
  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="nx-dialog" role="alertdialog" aria-modal="true" aria-labelledby="np-title" aria-describedby="np-help" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
        <h2 id="np-title">{t(`np.${action}.title`, { name: node.nom })}</h2>
        <p id="np-help" className="nx-muted" style={{ margin: 0 }}>{t(`np.${action}.help`)}</p>
        {running.length > 0 && (
          <div className="nx-notice nx-notice--warning" role="status">
            {t("np.running", { list: running.join(", ") })}
            <label className="nx-check" style={{ marginTop: "var(--space-2)" }}><input type="checkbox" checked={stopGuests} onChange={(e) => setStopGuests(e.target.checked)} disabled={busy} /> {t("np.stopGuests")}</label>
          </div>
        )}
        <label className="nx-dialog-field">{t("np.type", { name: hostname ?? "…" })}
          <input ref={first} className="nx-input nx-mono" value={typed} onChange={(e) => setTyped(e.target.value)} disabled={busy} autoComplete="off" spellCheck={false} />
        </label>
        <div className="nx-dialog-actions">
          <button type="button" className="nx-btn" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--danger" disabled={busy || !hostname || typed.trim() !== hostname} onClick={go}>{t(`np.${action}.go`)}</button>
        </div>
      </div>
    </div>
  );
}

// Mounted once in the app: the node menus open it with an "nx:node-power" event ({ node, action }).
export default function NodePowerHost() {
  const nodes = useInfraStore((s) => s.nodes);
  const [req, setReq] = useState(null);
  useEffect(() => {
    const on = (e) => setReq(e.detail);
    window.addEventListener("nx:node-power", on);
    return () => window.removeEventListener("nx:node-power", on);
  }, []);
  const node = req && nodes.find((n) => n.id === req.node);
  if (!node) return null;
  return <NodePowerDialog node={node} action={req.action} onClose={() => setReq(null)} />;
}
