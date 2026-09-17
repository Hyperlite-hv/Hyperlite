import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, ShieldOff, LifeBuoy, RefreshCw } from "lucide-react";
import StatusBadge from "../../components/StatusBadge";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchHaProtected, disableHa, recoverHa } from "../../api/client";

function formatDate(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// Chantier 17 : liste les VM protegees (GET /ha) avec le statut REEL de
// leur nœud, et permet a un admin de declencher une recuperation manuelle
// -- jamais automatique, voir app/core/ha.py pour pourquoi (pas de
// fencing : redemarrer automatiquement une VM protegee pendant que
// l'original tourne encore sur le meme disque partage corromprait les
// donnees, testè reellement en developpant ce chantier).
export default function HaTab() {
  const nodes = useInfraStore((s) => s.nodes);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [recoverTarget, setRecoverTarget] = useState({}); // vm_name -> nœud choisi
  const [busy, setBusy] = useState(null);

  const reload = useCallback(async () => {
    try { setRows(await fetchHaProtected()); }
    catch (e) { pushToast({ kind: "error", title: "Erreur HA", message: e.message }); }
  }, [pushToast]);

  useEffect(() => {
    reload();
    const id = setInterval(reload, 15000);
    return () => clearInterval(id);
  }, [reload]);

  async function handleDisable(vmName) {
    if (!window.confirm(`Désactiver la protection HA de '${vmName}' ?`)) return;
    try {
      await disableHa(vmName);
      pushToast({ kind: "success", title: "Protection désactivée", message: vmName });
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    }
  }

  async function handleRecover(vmName) {
    const target = recoverTarget[vmName];
    if (!target) return;
    setBusy(vmName);
    try {
      await recoverHa(vmName, target);
      pushToast({ kind: "success", title: "VM récupérée", message: `${vmName} sur ${target}` });
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la récupération", message: e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5">
      <div className="card p-4">
        <p className="text-sm text-anthracite-300">
          Les VM protégées ci-dessous doivent avoir tous leurs disques sur un pool de stockage <b>partagé</b> (NFS,
          onglet Stockage). Si le nœud d'une VM protégée tombe, une alerte apparaît ici — la récupération vers un
          autre nœud reste toujours <b>déclenchée manuellement</b> par un admin (jamais automatique, pour éviter
          tout risque de corruption si le nœud n'est en fait que temporairement injoignable).
        </p>
      </div>

      <div className="card divide-y divide-anthracite-600">
        <div className="grid grid-cols-5 gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
          <span>VM</span><span>Nœud actuel</span><span>Statut du nœud</span><span>Dernière synchro</span><span />
        </div>
        {rows == null && <div className="px-4 py-3 text-sm text-anthracite-400">Chargement...</div>}
        {rows && rows.length === 0 && (
          <div className="px-4 py-6 text-sm text-anthracite-400 text-center">
            Aucune VM protégée. Activez la protection HA depuis l'onglet Résumé d'une VM active (disque sur pool partagé requis).
          </div>
        )}
        {rows && rows.map((r) => {
          const down = r.statut_noeud === "hors_ligne";
          const targets = nodes.filter((n) => n.id !== r.node && n.etat === "online");
          return (
            <div key={r.vm_name} className="grid grid-cols-5 gap-2 px-4 py-3 text-sm items-center">
              <span className="text-anthracite-100 font-medium flex items-center gap-2">
                {down ? <ShieldOff size={14} className="text-status-error shrink-0" /> : <ShieldCheck size={14} className="text-status-running shrink-0" />}
                {r.vm_name}
              </span>
              <span className="text-anthracite-300">{r.node}</span>
              <span><StatusBadge etat={down ? "erreur" : "actif"} /></span>
              <span className="text-anthracite-400 text-xs font-mono">{formatDate(r.last_synced_at)}</span>
              <div className="flex items-center justify-end gap-2">
                {down && (
                  <>
                    <select
                      className="input w-auto text-xs py-1"
                      value={recoverTarget[r.vm_name] || ""}
                      onChange={(e) => setRecoverTarget({ ...recoverTarget, [r.vm_name]: e.target.value })}
                    >
                      <option value="">Récupérer sur…</option>
                      {targets.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
                    </select>
                    <button
                      className="btn-primary !py-1"
                      disabled={!recoverTarget[r.vm_name] || busy === r.vm_name}
                      onClick={() => handleRecover(r.vm_name)}
                    >
                      <LifeBuoy size={13} /> {busy === r.vm_name ? "..." : "Récupérer"}
                    </button>
                  </>
                )}
                <button className="btn-secondary !py-1" onClick={() => handleDisable(r.vm_name)}>
                  <ShieldOff size={13} /> Désactiver
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <button className="text-xs text-accent-blue hover:underline flex items-center gap-1.5" onClick={reload}>
        <RefreshCw size={12} /> Actualiser
      </button>
    </div>
  );
}
