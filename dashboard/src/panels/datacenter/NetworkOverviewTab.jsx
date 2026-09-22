import LoadingState from "../../components/LoadingState";
import { useCallback, useEffect, useState } from "react";
import { Network, Plus, Trash2 } from "lucide-react";
import {
  fetchNetworks, fetchNetworkDetail, createNetwork, deleteNetwork,
  fetchNetworkFirewall, setNetworkFirewall,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import FirewallRulesEditor from "../../components/FirewallRulesEditor";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// Local component (not exported) rather than an inline arrow function in the
// .map() below: useCallback needs to be kept stable PER network (same reason as
// VMHardwareTab.jsx). Without it, FirewallRulesEditor would trigger a GET on every
// re-render of the whole NetworkOverviewTab (e.g. a toast pushed by any other
// expanded network), not only when this specific network changes.
function NetworkFirewallSection({ name, isAdmin }) {
  const fetchConfig = useCallback(() => fetchNetworkFirewall(name), [name]);
  const saveConfig = useCallback((config) => setNetworkFirewall(name, config), [name]);
  return <FirewallRulesEditor title="Network firewall" fetchConfig={fetchConfig} saveConfig={saveConfig} isAdmin={isAdmin} />;
}

// Real: GET/POST/DELETE /networks (see app/routers/network.py): a vSphere
// Networking-style overview: virtual networks + connected VMs + DHCP leases.
export default function NetworkOverviewTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [networks, setNetworks] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [detail, setDetail] = useState(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [form, setForm] = useState({ name: "", mode: "isole", subnet_address: "192.168.150.1", dhcp_start: "192.168.150.10", dhcp_end: "192.168.150.100", bridge_name: "" });
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    fetchNetworks().then(setNetworks).catch((e) => pushToast({ kind: "error", title: "Networks error", message: e.message }));
  }, [pushToast]);

  useEffect(() => { reload(); }, [reload]);

  async function toggleExpand(name) {
    if (expanded === name) { setExpanded(null); return; }
    setExpanded(name);
    try { setDetail(await fetchNetworkDetail(name)); }
    catch (e) { pushToast({ kind: "error", title: "Network detail error", message: e.message }); }
  }

  async function handleCreate() {
    setBusy(true);
    try {
      await createNetwork({
        name: form.name, mode: form.mode,
        subnet_address: form.mode !== "bridge" ? form.subnet_address : undefined,
        dhcp_start: form.mode !== "bridge" ? form.dhcp_start : undefined,
        dhcp_end: form.mode !== "bridge" ? form.dhcp_end : undefined,
        bridge_name: form.mode === "bridge" ? form.bridge_name : undefined,
      });
      pushToast({ kind: "success", title: "Network created", message: form.name });
      setCreating(false);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(name) {
    setPendingDelete(null);
    try {
      await deleteNetwork(name);
      pushToast({ kind: "success", title: "Network deleted", message: name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Deletion failed", message: e.message });
    }
  }

  if (networks == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  return (
    <div className="space-y-3">
      {isAdmin && (
        <Button onClick={() => setCreating((c) => !c)}>
          <Plus /> Create a virtual network
        </Button>
      )}

      {creating && (
        <Card className="p-4 space-y-2 animate-in fade-in-0 slide-in-from-top-1 duration-150">
          <div className="grid grid-cols-2 gap-2">
            <Input aria-label="Name" placeholder="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <Select value={form.mode} onValueChange={(v) => setForm((f) => ({ ...f, mode: v }))}>
              <SelectTrigger aria-label="Network mode"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="isole">Isolated (no external access)</SelectItem>
                <SelectItem value="nat">NAT (outbound through the host)</SelectItem>
                <SelectItem value="bridge">Bridge to an existing physical network</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {form.mode === "bridge" ? (
            <Input aria-label="Host bridge name (e.g. br0)" className="w-full" placeholder="Host bridge name (e.g. br0)" value={form.bridge_name} onChange={(e) => setForm((f) => ({ ...f, bridge_name: e.target.value }))} />
          ) : (
            <div className="grid grid-cols-3 gap-2">
              <Input aria-label="Gateway (e.g. 192.168.150.1)" placeholder="Gateway (e.g. 192.168.150.1)" value={form.subnet_address} onChange={(e) => setForm((f) => ({ ...f, subnet_address: e.target.value }))} />
              <Input aria-label="DHCP start" placeholder="DHCP start" value={form.dhcp_start} onChange={(e) => setForm((f) => ({ ...f, dhcp_start: e.target.value }))} />
              <Input aria-label="DHCP end" placeholder="DHCP end" value={form.dhcp_end} onChange={(e) => setForm((f) => ({ ...f, dhcp_end: e.target.value }))} />
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={() => setCreating(false)}>Cancel</Button>
            <Button disabled={busy || !form.name} onClick={handleCreate}>Create</Button>
          </div>
        </Card>
      )}

      <Card className="p-0 divide-y divide-border">
        {networks.map((n) => (
          <div key={n.nom}>
            <div className="flex items-center gap-3 px-4 py-2.5 transition-colors duration-150 hover:bg-muted/40">
              <button className="flex flex-1 items-center gap-3 text-left" aria-expanded={expanded === n.nom} onClick={() => toggleExpand(n.nom)}>
                <Network size={14} className="text-muted-foreground shrink-0" />
                <span className="text-sm text-foreground">{n.nom}</span>
                <span className="text-xs text-muted-foreground">{n.type}{n.pont ? ` -- ${n.pont}` : ""}</span>
                <span className={`text-xs ${n.actif ? "text-status-running" : "text-status-stopped"}`}>{n.actif ? "active" : "stopped"}</span>
              </button>
              {isAdmin && !["default", "hyperlite-isolated"].includes(n.nom) && (
                <Button aria-label={`Delete network ${n.nom}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => setPendingDelete(n.nom)}><Trash2 size={13} /></Button>
              )}
            </div>
            {expanded === n.nom && detail && (
              <div className="px-8 pb-3 text-xs text-muted-foreground space-y-1 animate-in fade-in-0 duration-150">
                <div>Network: {detail.reseau ? `${detail.reseau.adresse}/${detail.reseau.masque}` : "--"}</div>
                <div>Active DHCP leases:</div>
                {(detail.baux_dhcp || []).length === 0 && <div className="pl-3">None</div>}
                {(detail.baux_dhcp || []).map((b, i) => (
                  <div key={i} className="pl-3 font-mono">{b.ip} — {b.mac} {b.hostname ? `(${b.hostname})` : ""}</div>
                ))}
                <div className="pt-2">
                  <NetworkFirewallSection name={n.nom} isAdmin={isAdmin} />
                </div>
              </div>
            )}
          </div>
        ))}
      </Card>
      <ConfirmDialog
        open={!!pendingDelete}
        title={`Delete network '${pendingDelete}'?`}
        message="The virtual network is removed from libvirt. VMs attached to it will lose their connectivity."
        confirmLabel="Delete"
        onConfirm={() => handleDelete(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
