import { useCallback, useEffect, useState } from "react";
import { Network, Plus, Trash2 } from "lucide-react";
import {
  fetchNetworks, fetchNetworkDetail, createNetwork, deleteNetwork,
  fetchNetworkFirewall, setNetworkFirewall,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import FirewallRulesEditor from "../../components/FirewallRulesEditor";

// Composant local (pas exporte) plutot qu'un inline arrow function dans le
// .map() plus bas : useCallback a besoin d'etre garde stable PAR reseau
// (meme raison que VMHardwareTab.jsx) -- sans ca, FirewallRulesEditor
// relancerait un GET a chaque re-render de NetworkOverviewTab entier (ex.
// un toast pousse par n'importe quel autre reseau developpe), pas
// seulement quand ce reseau precis change.
function NetworkFirewallSection({ name, isAdmin }) {
  const fetchConfig = useCallback(() => fetchNetworkFirewall(name), [name]);
  const saveConfig = useCallback((config) => setNetworkFirewall(name, config), [name]);
  return <FirewallRulesEditor title="Pare-feu réseau" fetchConfig={fetchConfig} saveConfig={saveConfig} isAdmin={isAdmin} />;
}

// Reel : GET/POST/DELETE /networks (voir app/routers/network.py, chantier 9)
// -- vue d'ensemble façon vSphere Networking : reseaux virtuels + VM
// connectees + baux DHCP.
export default function NetworkOverviewTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [networks, setNetworks] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [detail, setDetail] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", mode: "isole", subnet_address: "192.168.150.1", dhcp_start: "192.168.150.10", dhcp_end: "192.168.150.100", bridge_name: "" });
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    fetchNetworks().then(setNetworks).catch((e) => pushToast({ kind: "error", title: "Erreur réseaux", message: e.message }));
  }, [pushToast]);

  useEffect(() => { reload(); }, [reload]);

  async function toggleExpand(name) {
    if (expanded === name) { setExpanded(null); return; }
    setExpanded(name);
    try { setDetail(await fetchNetworkDetail(name)); }
    catch (e) { pushToast({ kind: "error", title: "Erreur détail réseau", message: e.message }); }
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
      pushToast({ kind: "success", title: "Réseau créé", message: form.name });
      setCreating(false);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la création", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete(name) {
    try {
      await deleteNetwork(name);
      pushToast({ kind: "success", title: "Réseau supprimé", message: name });
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la suppression", message: e.message });
    }
  }

  if (networks == null) return <div className="card p-4 text-sm text-anthracite-400">Chargement...</div>;

  return (
    <div className="space-y-3">
      {isAdmin && (
        <button className="btn-primary" onClick={() => setCreating((c) => !c)}>
          <Plus size={14} /> Créer un réseau virtuel
        </button>
      )}

      {creating && (
        <div className="card p-4 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input className="input" placeholder="Nom" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <select className="input" value={form.mode} onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value }))}>
              <option value="isole">Isolé (pas de sortie externe)</option>
              <option value="nat">NAT (sortie via l'hôte)</option>
              <option value="bridge">Pont vers un réseau physique existant</option>
            </select>
          </div>
          {form.mode === "bridge" ? (
            <input className="input w-full" placeholder="Nom du pont hôte (ex. br0)" value={form.bridge_name} onChange={(e) => setForm((f) => ({ ...f, bridge_name: e.target.value }))} />
          ) : (
            <div className="grid grid-cols-3 gap-2">
              <input className="input" placeholder="Passerelle (ex. 192.168.150.1)" value={form.subnet_address} onChange={(e) => setForm((f) => ({ ...f, subnet_address: e.target.value }))} />
              <input className="input" placeholder="DHCP début" value={form.dhcp_start} onChange={(e) => setForm((f) => ({ ...f, dhcp_start: e.target.value }))} />
              <input className="input" placeholder="DHCP fin" value={form.dhcp_end} onChange={(e) => setForm((f) => ({ ...f, dhcp_end: e.target.value }))} />
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <button className="btn-secondary" onClick={() => setCreating(false)}>Annuler</button>
            <button className="btn-primary" disabled={busy || !form.name} onClick={handleCreate}>Créer</button>
          </div>
        </div>
      )}

      <div className="card divide-y divide-anthracite-600">
        {networks.map((n) => (
          <div key={n.nom}>
            <button className="w-full flex items-center gap-3 px-4 py-2.5 text-left" onClick={() => toggleExpand(n.nom)}>
              <Network size={14} className="text-anthracite-400 shrink-0" />
              <span className="text-sm text-anthracite-100">{n.nom}</span>
              <span className="text-xs text-anthracite-400">{n.type}{n.pont ? ` -- ${n.pont}` : ""}</span>
              <span className={`text-xs ${n.actif ? "text-status-running" : "text-status-stopped"}`}>{n.actif ? "actif" : "arrêté"}</span>
              {isAdmin && !["default", "hyperlite-isolated"].includes(n.nom) && (
                <button className="btn-danger ml-auto" onClick={(e) => { e.stopPropagation(); handleDelete(n.nom); }}><Trash2 size={13} /></button>
              )}
            </button>
            {expanded === n.nom && detail && (
              <div className="px-8 pb-3 text-xs text-anthracite-400 space-y-1">
                <div>Réseau : {detail.reseau ? `${detail.reseau.adresse}/${detail.reseau.masque}` : "--"}</div>
                <div>Baux DHCP actifs :</div>
                {(detail.baux_dhcp || []).length === 0 && <div className="pl-3">Aucun</div>}
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
      </div>
    </div>
  );
}
