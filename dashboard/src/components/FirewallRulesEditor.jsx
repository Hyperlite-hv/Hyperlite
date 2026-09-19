import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Plus, Trash2, Save } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

const PROTOCOLS = ["tcp", "udp", "icmp", "all"];

// Rules editor shared between the PER-VM firewall (nwfilter, VMHardwareTab.jsx)
// and the NETWORK firewall (iptables/bridge, NetworkOverviewTab.jsx): the same data
// shape (FirewallConfig/FirewallRule on the backend, see app/routers/vms.py and
// app/core/network_firewall.py); only the real target of the rules changes,
// invisibly from the UI. `fetchConfig`/`saveConfig` carry the difference.
export default function FirewallRulesEditor({ title, fetchConfig, saveConfig, isAdmin }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [config, setConfig] = useState(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    fetchConfig().then(setConfig).catch((e) => pushToast({ kind: "error", title: "Firewall error", message: e.message }));
  }, [fetchConfig, pushToast]);

  useEffect(() => { reload(); }, [reload]);

  if (!config) return <div className="px-4 py-3 text-sm text-anthracite-400">Loading...</div>;

  function updateRule(i, patch) {
    setConfig((c) => ({ ...c, rules: c.rules.map((r, idx) => (idx === i ? { ...r, ...patch } : r)) }));
  }
  function addRule() {
    setConfig((c) => ({ ...c, rules: [...c.rules, { action: "accept", direction: "in", protocol: "tcp", port: null }] }));
  }
  function removeRule(i) {
    setConfig((c) => ({ ...c, rules: c.rules.filter((_, idx) => idx !== i) }));
  }

  async function handleSave() {
    setBusy(true);
    try {
      await saveConfig(config);
      pushToast({ kind: "success", title: "Firewall applied", message: title });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-anthracite-600">
        <ShieldCheck size={15} className="text-anthracite-400" />
        <h3 className="text-sm font-semibold text-anthracite-100">{title}</h3>
        <select
          className="input ml-auto w-40" disabled={!isAdmin}
          value={config.default_policy} onChange={(e) => setConfig((c) => ({ ...c, default_policy: e.target.value }))}
        >
          <option value="accept">Default: allow</option>
          <option value="drop">Default: block</option>
        </select>
      </div>
      <div className="divide-y divide-anthracite-600">
        {config.rules.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">No rules: all traffic follows the default policy.</div>}
        {config.rules.map((rule, i) => (
          <div key={i} className="flex items-center gap-2 px-4 py-2 text-sm">
            <select className="input w-28" disabled={!isAdmin} value={rule.action} onChange={(e) => updateRule(i, { action: e.target.value })}>
              <option value="accept">Allow</option>
              <option value="drop">Block</option>
            </select>
            <select className="input w-24" disabled={!isAdmin} value={rule.direction} onChange={(e) => updateRule(i, { direction: e.target.value })}>
              <option value="in">Inbound</option>
              <option value="out">Outbound</option>
              <option value="inout">Both</option>
            </select>
            <select className="input w-24" disabled={!isAdmin} value={rule.protocol} onChange={(e) => updateRule(i, { protocol: e.target.value })}>
              {PROTOCOLS.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
            {(rule.protocol === "tcp" || rule.protocol === "udp") && (
              <input
                type="number" min={1} max={65535} placeholder="port" className="input w-24" disabled={!isAdmin}
                value={rule.port ?? ""} onChange={(e) => updateRule(i, { port: e.target.value ? Number(e.target.value) : null })}
              />
            )}
            {isAdmin && (
              <button aria-label="Delete" className="btn-danger ml-auto" onClick={() => removeRule(i)}><Trash2 size={13} /></button>
            )}
          </div>
        ))}
      </div>
      {isAdmin && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-anthracite-600">
          <button className="btn-secondary" onClick={addRule}><Plus size={13} /> Add a rule</button>
          <button className="btn-primary" disabled={busy} onClick={handleSave}><Save size={13} /> Apply</button>
        </div>
      )}
    </div>
  );
}
