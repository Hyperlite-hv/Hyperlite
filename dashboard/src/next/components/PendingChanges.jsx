import { useEffect, useState } from "react";
import { fetchVMPendingChanges } from "../../api/client";
import { useT } from "../i18n";

const POLL_MS = 15000;

// Settings changed on a running VM that its next start applies (the running definition differs from the saved one).
// Shown on every tab of the VM: a change made on one page stays visible until the VM is stopped and started again.
// Read again when the VM, its state or the page changes, and every 15 s for changes made elsewhere.
export default function PendingChanges({ vm, tab }) {
  const t = useT();
  const [changes, setChanges] = useState([]);
  const [open, setOpen] = useState(false);
  const running = vm.etat === "actif";
  useEffect(() => {
    if (!running) { setChanges([]); return undefined; }
    let alive = true;
    const load = () => fetchVMPendingChanges(vm.nom, vm.node)
      .then((r) => alive && setChanges(r.changements || []))
      // A failed read leaves the last answer: this banner is advice, never a reason to show an error on each page.
      .catch(() => {});
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [vm.nom, vm.node, running, tab]);
  if (!running || changes.length === 0) return null;
  const label = (c) => t(`pc.${c.cle}`, { x: c.objet || "" });
  return (
    <div className="nx-pending" role="status">
      <div className="nx-pending-h">
        <span>{t("pc.summary", { list: changes.map(label).join(", ") })}</span>
        <button type="button" className="nx-btn nx-btn--ghost" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{t(open ? "pc.hide" : "pc.show")}</button>
      </div>
      {open && (
        <table className="nx-table nx-pending-t">
          <thead><tr><th scope="col">{t("pc.setting")}</th><th scope="col">{t("pc.now")}</th><th scope="col">{t("pc.next")}</th></tr></thead>
          <tbody>
            {changes.map((c) => (
              <tr key={`${c.cle}-${c.objet || ""}`}>
                <th scope="row">{label(c)}</th>
                <td className="nx-mono">{c.actuel ?? <span className="nx-muted">{t("pc.absent")}</span>}</td>
                <td className="nx-mono">{c.prochain ?? <span className="nx-muted">{t("pc.removed")}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
