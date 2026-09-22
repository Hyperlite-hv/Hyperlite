import LoadingState from "./LoadingState";
import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Plus, Trash2, Save } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

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

  if (!config) return <div className="px-4 py-3 text-sm text-muted-foreground"><LoadingState /></div>;

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
    <Card className="p-0">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
        <ShieldCheck size={15} className="text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <Select disabled={!isAdmin} value={config.default_policy} onValueChange={(v) => setConfig((c) => ({ ...c, default_policy: v }))}>
          <SelectTrigger aria-label="Default firewall policy" className="ml-auto w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="accept">Default: allow</SelectItem>
            <SelectItem value="drop">Default: block</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="divide-y divide-border">
        {config.rules.length === 0 && <div className="px-4 py-3 text-sm text-muted-foreground">No rules: all traffic follows the default policy.</div>}
        {config.rules.map((rule, i) => (
          <div key={i} className="flex items-center gap-2 px-4 py-2 text-sm">
            <Select disabled={!isAdmin} value={rule.action} onValueChange={(v) => updateRule(i, { action: v })}>
              <SelectTrigger aria-label="Rule action" className="w-28"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="accept">Allow</SelectItem>
                <SelectItem value="drop">Block</SelectItem>
              </SelectContent>
            </Select>
            <Select disabled={!isAdmin} value={rule.direction} onValueChange={(v) => updateRule(i, { direction: v })}>
              <SelectTrigger aria-label="Rule direction" className="w-24"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="in">Inbound</SelectItem>
                <SelectItem value="out">Outbound</SelectItem>
                <SelectItem value="inout">Both</SelectItem>
              </SelectContent>
            </Select>
            <Select disabled={!isAdmin} value={rule.protocol} onValueChange={(v) => updateRule(i, { protocol: v })}>
              <SelectTrigger aria-label="Rule protocol" className="w-24"><SelectValue /></SelectTrigger>
              <SelectContent>
                {PROTOCOLS.map((p) => <SelectItem key={p} value={p}>{p.toUpperCase()}</SelectItem>)}
              </SelectContent>
            </Select>
            {(rule.protocol === "tcp" || rule.protocol === "udp") && (
              <Input aria-label="port"
                type="number" min={1} max={65535} placeholder="port" className="w-24" disabled={!isAdmin}
                value={rule.port ?? ""} onChange={(e) => updateRule(i, { port: e.target.value ? Number(e.target.value) : null })}
              />
            )}
            {isAdmin && (
              <Button aria-label={`Remove rule ${i + 1}`} size="icon" variant="outline" className="ml-auto size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => removeRule(i)}><Trash2 size={13} /></Button>
            )}
          </div>
        ))}
      </div>
      {isAdmin && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-border">
          <Button variant="secondary" onClick={addRule}><Plus /> Add a rule</Button>
          <Button disabled={busy} onClick={handleSave}><Save /> Apply</Button>
        </div>
      )}
    </Card>
  );
}
