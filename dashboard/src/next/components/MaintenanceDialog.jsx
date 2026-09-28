import { useEffect, useRef, useState } from "react";
import { enterNodeMaintenance, fetchDrainPlan, leaveNodeMaintenance } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";

// Put a node in maintenance: pick where its running VMs go (or nowhere), read what the drain will do (the server's
// read-only plan: which VMs move, which stay and why), then confirm. The server checks everything again.
export function MaintenanceDialog({ node, targets, onClose }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const [target, setTarget] = useState(targets.length === 1 ? targets[0].id : "");
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const first = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);

  useEffect(() => { first.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);
  useEffect(() => {
    let alive = true;
    setPlan({ loading: true });
    fetchDrainPlan(node.id, target || null)
      .then((p) => alive && setPlan({ data: p }))
      .catch((e) => alive && setPlan({ error: errorMessage(e) }));
    return () => { alive = false; };
  }, [node.id, target]);

  async function start() {
    setBusy(true);
    try {
      const r = await enterNodeMaintenance(node.id, target || null);
      pushToast({ kind: "success", title: t("mt.entered"), message: t("mt.enteredMsg", { name: node.nom, n: r.migrables.length }) });
      onClose();
      refreshAll?.();
    } catch (e) {
      pushToast({ kind: "error", title: t("mt.failed"), message: errorMessage(e) });
      setBusy(false);
    }
  }

  const data = plan?.data;
  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="nx-dialog" role="dialog" aria-modal="true" aria-labelledby="mt-title" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
        <h2 id="mt-title">{t("mt.title", { name: node.nom })}</h2>
        <p className="nx-muted" style={{ margin: 0 }}>{t("mt.help")}</p>
        <label className="nx-dialog-field">{t("mt.target")}
          <select ref={first} className="nx-input" value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}>
            <option value="">{t("mt.noTarget")}</option>
            {targets.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
          </select>
        </label>
        {plan?.loading && <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("mt.checking")}</p>}
        {plan?.error && <p className="nx-notice nx-notice--warning" role="status" style={{ margin: 0 }}>{t("mt.checkFailed", { error: plan.error })}</p>}
        {data && (
          <>
            <section aria-labelledby="mt-move">
              <h3 id="mt-move" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-13)" }}>{t("mt.willMove", { n: data.migrables.length })}</h3>
              {data.migrables.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("mt.none")}</p>
                : <ul className="nx-list2" aria-labelledby="mt-move">{data.migrables.map((v) => <li key={v}><span className="nx-mono">{v}</span></li>)}</ul>}
            </section>
            <section aria-labelledby="mt-stay">
              <h3 id="mt-stay" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-13)" }}>{t("mt.willStay", { n: data.non_migrables.length })}</h3>
              {data.non_migrables.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("mt.none")}</p>
                : <ul className="nx-list2" aria-labelledby="mt-stay">{data.non_migrables.map((v) => <li key={v.nom}><div className="nx-list2-main"><span className="nx-mono">{v.nom}</span><div className="nx-list2-sub">{v.raison}</div></div></li>)}</ul>}
            </section>
          </>
        )}
        <div className="nx-dialog-actions">
          <button type="button" className="nx-btn" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" disabled={busy || plan?.loading} onClick={start}>{busy ? t("mt.starting") : t("mt.go")}</button>
        </div>
      </div>
    </div>
  );
}

export async function leaveMaintenance(node, t, pushToast, refreshAll) {
  if (!(await confirmAction({ title: t("mt.leaveTitle", { name: node.nom }), message: t("mt.leaveMsg"), confirmLabel: t("mt.leave") }))) return;
  try {
    await leaveNodeMaintenance(node.id);
    pushToast({ kind: "success", title: t("mt.left"), message: node.nom });
    refreshAll?.();
  } catch (e) { pushToast({ kind: "error", title: t("mt.leaveFailed"), message: errorMessage(e) }); }
}

// Mounted once in the app: the node menus open the dialog with an "nx:node-maintenance" event (a menu closes as
// soon as an item is chosen, so it cannot hold the dialog itself).
export default function MaintenanceHost() {
  const nodes = useInfraStore((s) => s.nodes);
  const [nodeId, setNodeId] = useState(null);
  useEffect(() => {
    const on = (e) => setNodeId(e.detail);
    window.addEventListener("nx:node-maintenance", on);
    return () => window.removeEventListener("nx:node-maintenance", on);
  }, []);
  const node = nodes.find((n) => n.id === nodeId);
  if (!node) return null;
  const targets = nodes.filter((n) => n.id !== node.id && n.etat === "online" && !n.maintenance);
  return <MaintenanceDialog node={node} targets={targets} onClose={() => setNodeId(null)} />;
}
