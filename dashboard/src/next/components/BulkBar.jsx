import { useEffect, useRef, useState } from "react";
import { MoveHorizontal, Play, Power, RotateCcw, Square, Trash2, X } from "lucide-react";
import { migrateVM } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { vmKey } from "../lib/vmId";
import { nameList, runBulk, splitSelection } from "../lib/bulk";

const ICONS = { start: Play, stop: Square, "force-stop": Power, restart: RotateCcw, migrate: MoveHorizontal, delete: Trash2 };
// Actions that cut a running guest or destroy it: their confirmation is styled as dangerous.
const DANGER = new Set(["force-stop", "restart", "delete"]);

// Destination of a bulk migration. The per-VM compatibility report of the single-VM dialog does not scale to a
// selection; the server runs the same checks on every VM and a refused one is listed in the results.
function BulkMigrateDialog({ vms, nodes, onPick, onClose }) {
  const t = useT();
  const targets = nodes.filter((n) => n.etat === "online" && !n.maintenance);
  const [target, setTarget] = useState("");
  const first = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  useEffect(() => { first.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);
  const moving = vms.filter((v) => v.node !== target).length;
  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="nx-dialog" role="dialog" aria-modal="true" aria-labelledby="bulkmig-title" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
        <h2 id="bulkmig-title">{t("bulk.migrate.title", { n: vms.length })}</h2>
        <p className="nx-muted" style={{ margin: 0 }}>{t("bulk.migrate.help")}</p>
        <label className="nx-dialog-field">{t("mig.target")}
          <select ref={first} className="nx-input" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">{t("mig.choose")}</option>
            {targets.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
          </select>
        </label>
        {target && moving < vms.length && <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("bulk.migrate.alreadyThere", { n: vms.length - moving })}</p>}
        <div className="nx-dialog-actions">
          <button type="button" className="nx-btn" onClick={onClose}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" disabled={!target || moving === 0} onClick={() => onPick(target)}>{t("bulk.migrate.go", { n: moving })}</button>
        </div>
      </div>
    </div>
  );
}

// Actions on several VMs at once: each button says how many of the selected VMs it applies to; the confirmation
// names them and the ones skipped; the calls run a few at a time and every failure is reported by name.
export default function BulkBar({ selected, caps, nodes, onClear }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const [busy, setBusy] = useState(null);
  const [migrating, setMigrating] = useState(false);
  const more = (n) => t("bulk.andMore", { n });

  async function execute(action, apply, skip, targetNode) {
    const label = t(`bulk.${action}.label`);
    const skipped = skip.length ? `\n${t("bulk.skipped", { n: skip.length, names: nameList(skip, more) })}` : "";
    const ok = await confirmAction({
      title: t("bulk.confirmTitle", { action: label, n: apply.length }),
      message: `${t(`bulk.${action}.message`, { node: nodes.find((n) => n.id === targetNode)?.nom || targetNode })}\n${nameList(apply, more)}${skipped}`,
      confirmLabel: t("bulk.confirmGo", { action: label, n: apply.length }),
      danger: DANGER.has(action),
    });
    if (!ok) return;
    setBusy(action);
    const one = (vm) => {
      if (action === "migrate") return migrateVM(vm.nom, targetNode, vm.node, false);
      // The store's action records the task, updates the list and rethrows a failure, so it is counted here.
      if (action === "force-stop") return runVMAction(vmKey(vm), "stop", { force: true });
      if (action === "stop") return runVMAction(vmKey(vm), "stop", { force: false });
      return runVMAction(vmKey(vm), action);
    };
    const { ok: done, failed } = await runBulk(apply, one);
    setBusy(null);
    if (done.length) pushToast({ kind: "success", title: t("bulk.done", { action: label, n: done.length, total: apply.length }), message: nameList(done, more) });
    if (failed.length) {
      pushToast({ kind: "error", title: t("bulk.failed", { action: label, n: failed.length }), message: failed.slice(0, 6).map(({ item, error }) => `${item.nom}: ${errorMessage(error, t("err.unknown"))}`).join("\n") });
    }
    if (action === "migrate") refreshAll?.();
    if (!failed.length) onClear();
  }

  const button = (action) => {
    const { apply, skip } = splitSelection(action, selected, caps);
    const Icon = ICONS[action];
    const disabled = apply.length === 0 || busy != null;
    const onClick = () => {
      if (disabled) return;
      if (action === "migrate") setMigrating(true);
      else execute(action, apply, skip);
    };
    return (
      <button key={action} type="button" className={`nx-btn nx-btn--sm${action === "delete" ? " nx-btn--danger" : ""}`} aria-disabled={disabled || undefined} onClick={onClick}
        title={apply.length === 0 ? t("bulk.noneApplies") : undefined}>
        <Icon size={14} aria-hidden="true" />{t(`bulk.${action}.label`)} <span className="nx-n">{apply.length}</span>
      </button>
    );
  };

  return (
    <div className="nx-bulkbar" role="region" aria-label={t("bulk.region")}>
      <span className="nx-bulkbar-n" role="status">{busy ? t("bulk.running", { action: t(`bulk.${busy}.label`) }) : t("bulk.selected", { n: selected.length })}</span>
      <div className="nx-inline" style={{ flexWrap: "wrap" }}>
        {["start", "stop", "restart", "force-stop"].map(button)}
        {caps.admin && nodes.length > 1 && button("migrate")}
        {caps.delete && button("delete")}
      </div>
      <span className="nx-sp" />
      <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={onClear} disabled={busy != null}><X size={14} aria-hidden="true" />{t("bulk.clear")}</button>
      {migrating && (
        <BulkMigrateDialog vms={splitSelection("migrate", selected, caps).apply} nodes={nodes} onClose={() => setMigrating(false)}
          onPick={(target) => {
            setMigrating(false);
            const { apply, skip } = splitSelection("migrate", selected, caps, target);
            execute("migrate", apply, skip, target);
          }} />
      )}
    </div>
  );
}
