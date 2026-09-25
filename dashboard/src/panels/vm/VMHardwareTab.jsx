import LoadingState from "../../components/LoadingState";
import DriversIsoControl from "../../components/DriversIsoControl";
import { confirmAction } from "../../store/useConfirmStore";
import { useCallback, useEffect, useState } from "react";
import { Cpu, MemoryStick, HardDrive, Network, Trash2, Plus } from "lucide-react";
import {
  fetchVMDisks, attachDisk, detachDisk, createVolume, fetchVolumes,
  fetchVMNetwork, attachInterface, detachInterface, fetchNetworks,
  fetchVMFirewall, setVMFirewall,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import FirewallRulesEditor from "../../components/FirewallRulesEditor";
import { useHostLimits } from "../../hooks/useHostLimits";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

// "Add a device" menu: computes the next free sdX device and detaches network
// interfaces by MAC address.
function nextScsiDev(disks) {
  const used = new Set(disks.filter((d) => /^sd[a-z]$/.test(d.cible)).map((d) => d.cible));
  for (const letter of "abcdefghijklmnopqrstuvwxyz") {
    if (!used.has("sd" + letter)) return "sd" + letter;
  }
  return null;
}

function DiskSection({ vmName, isAdmin }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const hostLimits = useHostLimits();
  const [disks, setDisks] = useState(null);
  const [volumes, setVolumes] = useState([]);
  const [source, setSource] = useState("__new__");
  const [newName, setNewName] = useState("");
  const [newSize, setNewSize] = useState(5);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [d, v] = await Promise.all([fetchVMDisks(vmName), fetchVolumes("default")]);
      setDisks(d);
      setVolumes(v.filter((x) => !x.utilise));
    } catch (e) {
      pushToast({ kind: "error", title: "Disks error", message: e.message });
    }
  }, [vmName, pushToast]);

  useEffect(() => { setNewName(`${vmName}-disk-${Date.now().toString().slice(-5)}`); reload(); }, [vmName, reload]);

  if (disks == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  const nextDev = nextScsiDev(disks);

  async function handleDetach(cible) {
    if (!(await confirmAction({ title: `Detach disk ${cible}?`, message: "The disk is removed from this VM's configuration.", confirmLabel: "Detach" }))) return;
    setBusy(true);
    try {
      await detachDisk(vmName, cible);
      pushToast({ kind: "success", title: "Disk detached", message: cible });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Detach failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleAttach() {
    if (!nextDev) return;
    setBusy(true);
    let createdVolume = null;
    try {
      let volName = source;
      if (source === "__new__") {
        if (!newName.trim()) { pushToast({ kind: "error", title: "Volume name required" }); setBusy(false); return; }
        const created = await createVolume("default", newName.trim(), newSize);
        volName = created.nom;
        createdVolume = created.nom;
      }
      await attachDisk(vmName, volName, nextDev);
      pushToast({ kind: "success", title: "Disk attached", message: `${volName} -> ${nextDev}` });
      // A fresh default name for the next new disk (the same name would be refused).
      setNewName(`${vmName}-disk-${Date.now().toString().slice(-5)}`);
      await reload();
    } catch (e) {
      // The volume may exist without being attached: say so and list it, so it can be attached again.
      pushToast({ kind: "error", title: "Attach failed", message: createdVolume ? `${e.message} — the volume "${createdVolume}" was created and is now listed as a free disk: select it to retry.` : e.message });
      if (createdVolume) { setSource(createdVolume); await reload(); }
    } finally { setBusy(false); }
  }

  return (
    <Card className="p-0">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
        <HardDrive size={15} className="text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">Disks</h3>
      </div>
      <div className="divide-y divide-border">
        {disks.map((d) => (
          <div key={d.cible} className="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors duration-150 hover:bg-muted/40">
            <span className="font-mono text-foreground w-16">{d.cible}</span>
            <span className="text-muted-foreground text-xs">{d.bus || "?"}</span>
            <span className="text-foreground/80 flex-1 truncate">{d.source || "Empty drive"}</span>
            {isAdmin && d.type !== "cdrom" && d.cible !== "vda" && d.cible !== "sda" && (
              <Button aria-label={`Detach disk ${d.cible}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" disabled={busy} onClick={() => handleDetach(d.cible)}><Trash2 size={13} /></Button>
            )}
          </div>
        ))}
      </div>

      {isAdmin && (
        <DriversIsoControl vmName={vmName} onChanged={reload} />
      )}
      {isAdmin && (
        <div className="px-4 py-3 border-t border-border space-y-2">
          <div className="text-xs font-medium text-foreground/90">Add a device</div>
          <NativeSelect aria-label="Disk to attach" className="w-full" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="__new__">+ New disk...</option>
            {volumes.map((v) => <option key={v.nom} value={v.nom}>{v.nom} ({v.capacite_go} GB)</option>)}
          </NativeSelect>
          {source === "__new__" && (
            <div className="flex gap-2">
              <Input aria-label="volume name" className="flex-1" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="volume name" />
              <Input aria-label="New disk size in GB" type="number" min={1} max={hostLimits?.disque_go.max} className="w-24" value={newSize} onChange={(e) => setNewSize(Number(e.target.value))} />
              <span className="self-center text-xs text-muted-foreground">GB</span>
            </div>
          )}
          <div className="flex items-center gap-2">
            {nextDev ? <span className="text-xs font-mono text-muted-foreground">will be attached as <b className="text-foreground/90">{nextDev}</b></span>
              : <span className="text-xs text-status-error">No letter available</span>}
            <Button variant="secondary" className="ml-auto" disabled={busy || !nextDev} onClick={handleAttach}><Plus /> Attach</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function NetworkSection({ vmName, isAdmin }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [info, setInfo] = useState(null);
  const [networks, setNetworks] = useState([]);
  const [addNet, setAddNet] = useState("");
  const [vlanTag, setVlanTag] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [i, n] = await Promise.all([fetchVMNetwork(vmName), fetchNetworks()]);
      setInfo(i);
      setNetworks(n);
      setAddNet((prev) => prev || n[0]?.nom || "");
    } catch (e) {
      pushToast({ kind: "error", title: "Network error", message: e.message });
    }
  }, [vmName, pushToast]);

  useEffect(() => { reload(); }, [vmName, reload]);

  if (info == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  async function handleDetach(mac) {
    if (!(await confirmAction({ title: `Remove network interface ${mac}?`, message: "The VM loses this network interface.", confirmLabel: "Remove" }))) return;
    setBusy(true);
    try {
      await detachInterface(vmName, mac);
      pushToast({ kind: "success", title: "Interface detached", message: mac });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Detach failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleAttach() {
    setBusy(true);
    try {
      await attachInterface(vmName, addNet, vlanTag ? Number(vlanTag) : null);
      pushToast({ kind: "success", title: "Interface added", message: addNet });
      setVlanTag("");
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Add failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <Card className="p-0">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
        <Network size={15} className="text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">Network interfaces</h3>
      </div>
      <div className="divide-y divide-border">
        {info.interfaces.map((iface) => (
          <div key={iface.mac} className="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors duration-150 hover:bg-muted/40">
            <span className="text-foreground">{iface.reseau || "--"}</span>
            <span className="text-muted-foreground text-xs font-mono">{iface.mac}</span>
            {isAdmin && info.interfaces.length > 1 && (
              <Button aria-label={`Remove interface ${iface.mac}`} size="icon" variant="outline" className="ml-auto size-7 text-status-error border-status-error/30 hover:bg-status-error/10" disabled={busy} onClick={() => handleDetach(iface.mac)}><Trash2 size={13} /></Button>
            )}
          </div>
        ))}
      </div>
      {isAdmin && (
        <div className="flex items-center gap-2 px-4 py-3 border-t border-border">
          <NativeSelect aria-label="Network to attach" className="flex-1" value={addNet} onChange={(e) => setAddNet(e.target.value)}>
            {networks.map((n) => <option key={n.nom} value={n.nom}>{n.nom} ({n.type})</option>)}
          </NativeSelect>
          <Input aria-label="VLAN (optional)"
            type="number" min={1} max={4094} placeholder="VLAN (optional)" className="w-36"
            value={vlanTag} onChange={(e) => setVlanTag(e.target.value)}
          />
          <Button variant="secondary" disabled={busy} onClick={handleAttach}><Plus /> Add</Button>
        </div>
      )}
    </Card>
  );
}

export default function VMHardwareTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const fetchFirewall = useCallback(() => fetchVMFirewall(vm?.nom), [vm?.nom]);
  const saveFirewall = useCallback((config) => setVMFirewall(vm?.nom, config), [vm?.nom]);
  if (!vm) return null;

  return (
    <div className="space-y-4">
      <Card className="p-0 divide-y divide-border">
        <div className="flex items-center gap-3 px-4 py-3">
          <Cpu size={16} className="text-muted-foreground shrink-0" />
          <span className="text-sm text-foreground/90 flex-1">Processor</span>
          <span className="text-sm text-foreground font-mono">{vm.vcpu} vCPU</span>
        </div>
        <div className="flex items-center gap-3 px-4 py-3">
          <MemoryStick size={16} className="text-muted-foreground shrink-0" />
          <span className="text-sm text-foreground/90 flex-1">Memory</span>
          <span className="text-sm text-foreground font-mono">{vm.memoire_mo} MB</span>
        </div>
      </Card>

      <DiskSection vmName={vm.nom} isAdmin={isAdmin} />
      <NetworkSection vmName={vm.nom} isAdmin={isAdmin} />
      <FirewallRulesEditor title="Firewall (nwfilter)" fetchConfig={fetchFirewall} saveConfig={saveFirewall} isAdmin={isAdmin} />
    </div>
  );
}
