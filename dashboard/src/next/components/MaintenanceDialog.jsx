import { useEffect, useRef, useState } from "react";
import { enterNodeMaintenance, fetchDrainPlan, leaveNodeMaintenance } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";

// Not a node id (a node name starts with a letter or a digit): each VM where load balancing places it.
const SPREAD = "__spread__";

// Put a node in maintenance: pick where its running VMs go (one node, each where load balancing places it, or
// nowhere), read what the drain will do (the server's read-only plan: which VMs move where, which stay and why), then
// confirm. The server checks everything again.
export function MaintenanceDialog({ node, targets, onClose }) {
  const t = useT();
  const nodes = useInfraStore((s) => s.nodes);
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  // Spreading needs two members to choose from (load balancing reads the cluster members' loads).
  const canSpread = targets.filter((n) => n.member || n.id === "local").length >= 2;
  const [target, setTarget] = useState(canSpread ? SPREAD : targets.length === 1 ? targets[0].id : "");
  const spread = target === SPREAD;
  const nodeName = (id) => nodes.find((n) => n.id === id)?.name || id;
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const first = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);

  useEffect(() => { first.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);
  useEffect(() => {
    let alive = true;
    setPlan({ loading: true });
    fetchDrainPlan(node.id, spread ? null : target || null, spread)
      .then((p) => alive && setPlan({ data: p }))
      .catch((e) => alive && setPlan({ error: errorMessage(e) }));
    return () => { alive = false; };
  }, [node.id, target, spread]);

  async function start() {
    setBusy(true);
    try {
      const r = await enterNodeMaintenance(node.id, spread ? null : target || null, spread);
      pushToast({ kind: "success", title: t("mt.entered"), message: t("mt.enteredMsg", { name: node.name, n: r.migratable.length }) });
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
        <h2 id="mt-title">{t("mt.title", { name: node.name })}</h2>
        <p className="nx-muted" style={{ margin: 0 }}>{t("mt.help")}</p>
        <label className="nx-dialog-field">{t("mt.target")}
          <select ref={first} className="nx-input" value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}>
            {canSpread && <option value={SPREAD}>{t("mt.spread")}</option>}
            <option value="">{t("mt.noTarget")}</option>
            {targets.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
          </select>
        </label>
        {spread && <p className="nx-muted" style={{ margin: 0 }}>{t("mt.spreadHelp")}</p>}
        {plan?.loading && <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("mt.checking")}</p>}
        {plan?.error && <p className="nx-notice nx-notice--warning" role="status" style={{ margin: 0 }}>{t("mt.checkFailed", { error: plan.error })}</p>}
        {data && (
          <>
            <section aria-labelledby="mt-move">
              <h3 id="mt-move" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-13)" }}>{t("mt.willMove", { n: data.migratable.length })}</h3>
              {data.migratable.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("mt.none")}</p>
                : <ul className="nx-list2" aria-labelledby="mt-move">{data.migratable.map((v) => <li key={v}><span className="nx-mono">{v}</span>{data.targets?.[v] && <span className="nx-muted"> → {nodeName(data.targets[v])}</span>}</li>)}</ul>}
            </section>
            <section aria-labelledby="mt-stay">
              <h3 id="mt-stay" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-13)" }}>{t("mt.willStay", { n: data.not_migratable.length })}</h3>
              {data.not_migratable.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("mt.none")}</p>
                : <ul className="nx-list2" aria-labelledby="mt-stay">{data.not_migratable.map((v) => <li key={v.name}><div className="nx-list2-main"><span className="nx-mono">{v.name}</span><div className="nx-list2-sub">{v.reason}</div></div></li>)}</ul>}
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
  if (!(await confirmAction({ title: t("mt.leaveTitle", { name: node.name }), message: t("mt.leaveMsg"), confirmLabel: t("mt.leave") }))) return;
  try {
    await leaveNodeMaintenance(node.id);
    pushToast({ kind: "success", title: t("mt.left"), message: node.name });
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
  const targets = nodes.filter((n) => n.id !== node.id && n.state === "online" && !n.maintenance);
  return <MaintenanceDialog node={node} targets={targets} onClose={() => setNodeId(null)} />;
}
