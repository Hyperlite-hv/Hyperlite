import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Check, X } from "lucide-react";
import { fetchBackupSchedules, fetchNotificationChannels } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { requestIntent } from "../lib/intents";
import { useFreshness } from "../lib/inventory";

const HIDDEN_KEY = "hl.home.setupHidden";
const readHidden = () => { try { return localStorage.getItem(HIDDEN_KEY) === "1"; } catch { return false; } };
const asList = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []);

// The steps of a new installation, each checked from what already exists: a storage pool, a network, a first machine,
// a backup schedule, a notification channel and, optionally, a second node. Shown to administrators until every
// required step is done or the card is hidden (a per-browser choice).
export default function SetupChecklist() {
  const t = useT();
  const { nodes, vms, pools, networks, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, pools: s.storagePools, networks: s.networks, navigateTo: s.navigateTo })));
  const failing = useFreshness((s) => s.failing);
  const [hidden, setHidden] = useState(readHidden);
  const [schedules, setSchedules] = useState(null);
  const [channels, setChannels] = useState(null);
  useEffect(() => {
    if (hidden) return;
    fetchBackupSchedules().then((r) => setSchedules(asList(r).length)).catch(() => setSchedules(null));
    fetchNotificationChannels().then((r) => setChannels(asList(r).length)).catch(() => setChannels(null));
  }, [hidden]);

  const open = (svc, intent) => { if (intent) requestIntent(intent); navigateTo("datacenter", null, svc); };
  const steps = [
    { id: "pool", done: pools.length > 0, go: () => open("storage", "pool") },
    { id: "network", done: networks.length > 0, go: () => open("network", "network") },
    { id: "vm", done: vms.length > 0, go: () => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" })) },
    { id: "backup", done: schedules > 0, go: () => open("backups") },
    { id: "channel", done: channels > 0, go: () => open("notifications") },
    { id: "node", done: nodes.length > 1, optional: true, go: () => open("nodes", "node") },
  ];
  const required = steps.filter((s) => !s.optional);
  const done = required.filter((s) => s.done).length;
  // Nothing is claimed before the counts are in: a failed read leaves its step unchecked rather than hiding the card.
  // Nor from an inventory that is not loaded or not current (a cluster always has at least its own node).
  if (hidden || failing || nodes.length === 0 || (schedules === null && channels === null) || done === required.length) return null;
  const hide = () => { try { localStorage.setItem(HIDDEN_KEY, "1"); } catch { /* hidden for this page only */ } setHidden(true); };

  return (
    <section className="hc-card hc-setup" aria-labelledby="hc-setup-h">
      <h2 id="hc-setup-h">{t("setup.title")}
        <button type="button" className="hc-ibtn" aria-label={t("setup.hide")} title={t("setup.hide")} onClick={hide}><X size={16} aria-hidden="true" /></button>
      </h2>
      <p className="nx-muted">{t("setup.progress", { n: done, total: required.length })}</p>
      <span className="hc-cap-bar" role="progressbar" aria-label={t("setup.title")} aria-valuemin={0} aria-valuemax={required.length} aria-valuenow={done}><span style={{ width: `${(done / required.length) * 100}%` }} /></span>
      <ol className="hc-steps">
        {steps.map((s) => (
          <li key={s.id} data-done={s.done || undefined}>
            <span className="hc-step-mark" aria-hidden="true">{s.done ? <Check size={14} /> : null}</span>
            <span className="hc-step-t"><b>{t(`setup.${s.id}`)}{s.optional && <small> · {t("setup.optional")}</small>}</b><small>{t(`setup.${s.id}.d`)}</small></span>
            {s.done ? <span className="nx-sr">{t("setup.done")}</span> : <button type="button" className="nx-btn nx-btn--sm" onClick={s.go}>{t(`setup.${s.id}.go`)}</button>}
          </li>
        ))}
      </ol>
    </section>
  );
}
