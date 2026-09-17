import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Plus, Trash2, Save } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

const PROTOCOLS = ["tcp", "udp", "icmp", "all"];

// Editeur de regles factorise entre le pare-feu PAR VM (nwfilter,
// VMHardwareTab.jsx, chantier 9) et le pare-feu RESEAU (iptables/pont,
// NetworkOverviewTab.jsx, chantier 21) -- meme forme de donnees
// (FirewallConfig/FirewallRule cote backend, voir app/routers/vms.py et
// app/core/network_firewall.py), seule la cible reelle des regles change,
// invisible depuis l'UI. `fetchConfig`/`saveConfig` portent la difference.
export default function FirewallRulesEditor({ title, fetchConfig, saveConfig, isAdmin }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [config, setConfig] = useState(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    fetchConfig().then(setConfig).catch((e) => pushToast({ kind: "error", title: "Erreur pare-feu", message: e.message }));
  }, [fetchConfig, pushToast]);

  useEffect(() => { reload(); }, [reload]);

  if (!config) return <div className="px-4 py-3 text-sm text-anthracite-400">Chargement...</div>;

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
      pushToast({ kind: "success", title: "Pare-feu appliqué", message: title });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
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
          <option value="accept">Par défaut : autoriser</option>
          <option value="drop">Par défaut : bloquer</option>
        </select>
      </div>
      <div className="divide-y divide-anthracite-600">
        {config.rules.length === 0 && <div className="px-4 py-3 text-sm text-anthracite-400">Aucune règle -- tout le trafic suit la politique par défaut.</div>}
        {config.rules.map((rule, i) => (
          <div key={i} className="flex items-center gap-2 px-4 py-2 text-sm">
            <select className="input w-28" disabled={!isAdmin} value={rule.action} onChange={(e) => updateRule(i, { action: e.target.value })}>
              <option value="accept">Autoriser</option>
              <option value="drop">Bloquer</option>
            </select>
            <select className="input w-24" disabled={!isAdmin} value={rule.direction} onChange={(e) => updateRule(i, { direction: e.target.value })}>
              <option value="in">Entrant</option>
              <option value="out">Sortant</option>
              <option value="inout">Les deux</option>
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
              <button className="btn-danger ml-auto" onClick={() => removeRule(i)}><Trash2 size={13} /></button>
            )}
          </div>
        ))}
      </div>
      {isAdmin && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-anthracite-600">
          <button className="btn-secondary" onClick={addRule}><Plus size={13} /> Ajouter une règle</button>
          <button className="btn-primary" disabled={busy} onClick={handleSave}><Save size={13} /> Appliquer</button>
        </div>
      )}
    </div>
  );
}
