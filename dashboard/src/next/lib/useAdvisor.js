import { useCallback, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchApiTokens, fetchBackupSchedules, fetchNotificationChannels, fetchUsers } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { recommendations } from "./advisor";

const asList = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : null);
const KEEP_MS = 5 * 60_000;
let extra = null; // { admin, at, value }: what the inventory store does not hold, shared by the pages for a few minutes

async function readExtra(admin) {
  const soft = (p) => p.then(asList).catch(() => null);
  const [schedules, users, tokens, channels] = await Promise.all([
    soft(fetchBackupSchedules()),
    admin ? soft(fetchUsers()) : null, admin ? soft(fetchApiTokens()) : null, admin ? soft(fetchNotificationChannels()) : null,
  ]);
  return { scheduled: schedules && new Set(schedules.map((s) => s.vm_name)), users, tokens, channels: channels ? channels.length : null };
}
function fresh(admin) {
  return extra && extra.admin === admin && Date.now() - extra.at < KEEP_MS ? extra.value : null;
}

// The recommendations of the whole cluster, from the inventory and a few extra reads (administrators see the
// account and token rules too). `refresh` reads the extra sources again.
export function useAdvisor(admin) {
  const { nodes, vms, pools } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, pools: s.storagePools })));
  const [data, setData] = useState(() => fresh(admin));
  const load = useCallback(async (force) => {
    const hit = !force && fresh(admin);
    if (hit) { setData(hit); return; }
    const value = await readExtra(admin);
    extra = { admin, at: Date.now(), value };
    setData(value);
  }, [admin]);
  useEffect(() => { load(false); }, [load]);
  const list = data ? recommendations({ nodes, vms, pools, ...data }) : null;
  return { list, refresh: () => load(true) };
}

// Snoozed (until a date) and dismissed recommendations, by id, in this browser.
const HIDDEN_KEY = "hl.advisor.hidden";
export function readHiddenState() {
  try { const v = JSON.parse(localStorage.getItem(HIDDEN_KEY) || "{}"); return v && typeof v === "object" ? v : {}; } catch { return {}; }
}
export function useHiddenState() {
  const [state, setState] = useState(readHiddenState);
  const update = (id, value) => setState((prev) => {
    const next = { ...prev };
    if (value == null) delete next[id]; else next[id] = value;
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify(next)); } catch { /* kept for this page only */ }
    return next;
  });
  return [state, update];
}
