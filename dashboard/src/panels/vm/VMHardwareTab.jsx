import { useCallback, useEffect, useState } from "react";
import { Cpu, MemoryStick, HardDrive, Network, Trash2, Plus } from "lucide-react";
import {
  fetchVMDisks, attachDisk, detachDisk, createVolume, fetchVolumes,
  fetchVMNetwork, attachInterface, detachInterface, fetchNetworks,
} from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";

// Porte en React le menu "Ajouter un peripherique" deja construit et teste
// cote vanilla-JS (app/static/app.js: loadVMDisksTab/loadVMNetTab) -- memes
// endpoints reels, meme logique de calcul du prochain device sdX libre et de
// detachement par adresse MAC pour les interfaces.
function nextScsiDev(disks) {
  const used = new Set(disks.filter((d) => /^sd[a-z]$/.test(d.cible)).map((d) => d.cible));
  for (const letter of "abcdefghijklmnopqrstuvwxyz") {
    if (!used.has("sd" + letter)) return "sd" + letter;
  }
  return null;
}

function DiskSection({ vmName, isAdmin }) {
  const pushToast = useInfraStore((s) => s.pushToast);
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
      pushToast({ kind: "error", title: "Erreur disques", message: e.message });
    }
  }, [vmName, pushToast]);

  useEffect(() => { setNewName(`${vmName}-disk-${Date.now().toString().slice(-5)}`); reload(); }, [vmName, reload]);

  if (disks == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  const nextDev = nextScsiDev(disks);

  async function handleDetach(cible) {
    setBusy(true);
    try {
      await detachDisk(vmName, cible);
      pushToast({ kind: "success", title: "Disque détaché", message: cible });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec du détachement", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleAttach() {
    if (!nextDev) return;
    setBusy(true);
    try {
      let volName = source;
      if (source === "__new__") {
        if (!newName.trim()) { pushToast({ kind: "error", title: "Nom de volume requis" }); setBusy(false); return; }
        const created = await createVolume("default", newName.trim(), newSize);
        volName = created.nom;
      }
      await attachDisk(vmName, volName, nextDev);
      pushToast({ kind: "success", title: "Disque attaché", message: `${volName} -> ${nextDev}` });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de l'attachement", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-anthracite-600">
        <HardDrive size={15} className="text-anthracite-400" />
        <h3 className="text-sm font-semibold text-anthracite-100">Disques</h3>
      </div>
      <div className="divide-y divide-anthracite-600">
        {disks.map((d) => (
          <div key={d.cible} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <span className="font-mono text-anthracite-100 w-16">{d.cible}</span>
            <span className="text-anthracite-400 text-xs">{d.bus || "?"}</span>
            <span className="text-anthracite-300 flex-1 truncate">{d.type === "cdrom" ? "cloud-init / ISO" : d.source}</span>
            {isAdmin && d.type !== "cdrom" && d.cible !== "vda" && d.cible !== "sda" && (
              <button className="btn-danger" disabled={busy} onClick={() => handleDetach(d.cible)}><Trash2 size={13} /></button>
            )}
          </div>
        ))}
      </div>

      {isAdmin && (
        <div className="px-4 py-3 border-t border-anthracite-600 space-y-2">
          <div className="text-xs font-medium text-anthracite-300">Ajouter un périphérique</div>
          <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="__new__">+ Nouveau disque...</option>
            {volumes.map((v) => <option key={v.nom} value={v.nom}>{v.nom} ({v.capacite_go} Go)</option>)}
          </select>
          {source === "__new__" && (
            <div className="flex gap-2">
              <input className="input flex-1" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="nom du volume" />
              <input type="number" min={1} max={500} className="input w-24" value={newSize} onChange={(e) => setNewSize(Number(e.target.value))} />
              <span className="self-center text-xs text-anthracite-400">Go</span>
            </div>
          )}
          <div className="flex items-center gap-2">
            {nextDev ? <span className="text-xs font-mono text-anthracite-400">sera attaché en tant que <b className="text-anthracite-200">{nextDev}</b></span>
              : <span className="text-xs text-status-error">Plus de lettre disponible</span>}
            <button className="btn-secondary ml-auto" disabled={busy || !nextDev} onClick={handleAttach}><Plus size={13} /> Attacher</button>
          </div>
        </div>
      )}
    </div>
  );
}

function NetworkSection({ vmName, isAdmin }) {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [info, setInfo] = useState(null);
  const [networks, setNetworks] = useState([]);
  const [addNet, setAddNet] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [i, n] = await Promise.all([fetchVMNetwork(vmName), fetchNetworks()]);
      setInfo(i);
      setNetworks(n);
      setAddNet((prev) => prev || n[0]?.nom || "");
    } catch (e) {
      pushToast({ kind: "error", title: "Erreur réseau", message: e.message });
    }
  }, [vmName, pushToast]);

  useEffect(() => { reload(); }, [vmName, reload]);

  if (info == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  async function handleDetach(mac) {
    setBusy(true);
    try {
      await detachInterface(vmName, mac);
      pushToast({ kind: "success", title: "Interface détachée", message: mac });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec du détachement", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleAttach() {
    setBusy(true);
    try {
      await attachInterface(vmName, addNet);
      pushToast({ kind: "success", title: "Interface ajoutée", message: addNet });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de l'ajout", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-anthracite-600">
        <Network size={15} className="text-anthracite-400" />
        <h3 className="text-sm font-semibold text-anthracite-100">Interfaces réseau</h3>
      </div>
      <div className="divide-y divide-anthracite-600">
        {info.interfaces.map((iface) => (
          <div key={iface.mac} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <span className="text-anthracite-100">{iface.reseau || "--"}</span>
            <span className="text-anthracite-400 text-xs font-mono">{iface.mac}</span>
            {isAdmin && info.interfaces.length > 1 && (
              <button className="btn-danger ml-auto" disabled={busy} onClick={() => handleDetach(iface.mac)}><Trash2 size={13} /></button>
            )}
          </div>
        ))}
      </div>
      {isAdmin && (
        <div className="flex items-center gap-2 px-4 py-3 border-t border-anthracite-600">
          <select className="input flex-1" value={addNet} onChange={(e) => setAddNet(e.target.value)}>
            {networks.map((n) => <option key={n.nom} value={n.nom}>{n.nom} ({n.type})</option>)}
          </select>
          <button className="btn-secondary" disabled={busy} onClick={handleAttach}><Plus size={13} /> Ajouter</button>
        </div>
      )}
    </div>
  );
}

export default function VMHardwareTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  if (!vm) return null;

  return (
    <div className="space-y-4">
      <div className="card divide-y divide-anthracite-600">
        <div className="flex items-center gap-3 px-4 py-3">
          <Cpu size={16} className="text-anthracite-400 shrink-0" />
          <span className="text-sm text-anthracite-200 flex-1">Processeur</span>
          <span className="text-sm text-anthracite-100 font-mono">{vm.vcpu} vCPU</span>
        </div>
        <div className="flex items-center gap-3 px-4 py-3">
          <MemoryStick size={16} className="text-anthracite-400 shrink-0" />
          <span className="text-sm text-anthracite-200 flex-1">Mémoire</span>
          <span className="text-sm text-anthracite-100 font-mono">{vm.memoire_mo} Mo</span>
        </div>
      </div>

      <DiskSection vmName={vm.nom} isAdmin={isAdmin} />
      <NetworkSection vmName={vm.nom} isAdmin={isAdmin} />
    </div>
  );
}
