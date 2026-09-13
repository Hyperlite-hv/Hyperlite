import { useEffect, useState } from "react";
import GaugeRing from "../../components/GaugeRing";
import StatusBadge from "../../components/StatusBadge";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchHostMetricsHistory, fetchTasks } from "../../api/client";
import { formatMo, formatGo } from "../../utils/format";
import { statusColor } from "../../theme/colors";

function sumDefined(items, key) {
  const defined = items.filter((i) => i[key] != null);
  if (!defined.length) return null;
  return defined.reduce((a, i) => a + i[key], 0);
}

// Etats reels d'un domaine libvirt (STATE_NAMES, app/routers/vms.py) dans
// l'ordre d'affichage voulu -- "bloque"/"en_arret"/"inconnu" sont regroupes
// dans "Autre" plus bas (transitoires/rares, pas la peine d'une ligne dediee
// tant qu'aucune VM n'y est).
const VM_STATUS_ORDER = ["actif", "arrete", "en_pause", "suspendu", "plante"];

const TASK_LABELS = {
  create_vm: "Créer VM", delete_vm: "Supprimer VM", start_vm: "Démarrer VM",
  stop_vm: "Arrêter VM", restart_vm: "Redémarrer VM", clone_vm: "Cloner VM",
  auto_install: "Installation automatisée", create_snapshot: "Créer snapshot",
  backup_vm: "Sauvegarder VM", restore_backup: "Restaurer sauvegarde",
  export_vm: "Exporter VM", upload_vm_disk: "Téléverser disque",
  create_container: "Créer conteneur", upload_iso: "Téléverser ISO",
};
const STATUT_ETAT = { en_cours: "avertissement", termine: "actif", echec: "erreur", en_attente: "avertissement" };

function formatHeure(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// CPU/RAM reels du noeud local ("kvm-lab") via GET /host/metrics/history
// (collecte continue, chantier 10) -- fetchNodes() (api/client.js) pose
// volontairement ces champs a null (pas expose par GET /dashboard), ce qui
// affichait "n/a"/"Non exposé par /dashboard" en permanence ici meme apres
// le chantier 10. Meme source que NodeSummaryTab.jsx. Un seul noeud reel
// aujourd'hui (multi-noeuds distants, chantier 15, n'exposent pas encore
// leurs propres metriques) : les noeuds distants sans donnee restent "n/a",
// ils ne comptent simplement pas dans la moyenne/somme (sumDefined filtre
// deja les null).
export default function DatacenterSummaryTab() {
  const { nodes, vms, navigateTo } = useInfraStore((s) => ({ nodes: s.nodes, vms: s.vms, navigateTo: s.navigateTo }));
  const [localMetrics, setLocalMetrics] = useState(null);
  const [recentTasks, setRecentTasks] = useState(null);

  useEffect(() => {
    const load = () => fetchHostMetricsHistory("1h").then((rows) => setLocalMetrics(rows[rows.length - 1] || null)).catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const load = () => fetchTasks({ limit: 6 }).then(setRecentTasks).catch(() => {});
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  // Fusionne les vraies metriques locales dans la liste des noeuds (le
  // noeud local est toujours id "kvm-lab", voir fetchNodes()) avant tout
  // calcul -- une seule source de verite pour l'agregat ET le detail
  // par-noeud plus bas.
  const enrichedNodes = nodes.map((n) => (n.id !== "kvm-lab" || !localMetrics ? n : {
    ...n,
    cpu_utilisation: localMetrics.cpu_pct != null ? localMetrics.cpu_pct / 100 : n.cpu_utilisation,
    memoire_totale_mo: localMetrics.mem_total_mb ?? n.memoire_totale_mo,
    memoire_utilisee_mo: localMetrics.mem_used_mb ?? n.memoire_utilisee_mo,
  }));

  const totalRamMo = sumDefined(enrichedNodes, "memoire_totale_mo");
  const usedRamMo = sumDefined(enrichedNodes, "memoire_utilisee_mo");
  const totalDiskGo = sumDefined(enrichedNodes, "stockage_total_go");
  const usedDiskGo = sumDefined(enrichedNodes, "stockage_utilise_go");
  const cpuNodes = enrichedNodes.filter((n) => n.cpu_utilisation != null);
  const avgCpu = cpuNodes.length ? cpuNodes.reduce((a, n) => a + n.cpu_utilisation, 0) / cpuNodes.length : null;

  const totalVms = vms.length;
  const vmCounts = VM_STATUS_ORDER.map((etat) => ({ etat, count: vms.filter((v) => v.etat === etat).length }));
  const autreCount = totalVms - vmCounts.reduce((a, c) => a + c.count, 0);

  return (
    <div className="space-y-5">
      <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
        <GaugeRing
          label="CPU moyen" ratio={avgCpu}
          valueLabel={avgCpu != null ? `${Math.round(avgCpu * 100)} % · ${nodes.length} nœud(s)` : `${nodes.length} nœud(s)`}
          colorClass="text-accent-blue"
        />
        <GaugeRing
          label="RAM" ratio={totalRamMo != null && usedRamMo != null ? usedRamMo / totalRamMo : null}
          valueLabel={totalRamMo != null && usedRamMo != null ? `${formatMo(usedRamMo)} / ${formatMo(totalRamMo)}` : "n/a"}
          colorClass="text-accent-orange"
        />
        <GaugeRing
          label="Stockage" ratio={totalDiskGo != null && usedDiskGo != null ? usedDiskGo / totalDiskGo : null}
          valueLabel={totalDiskGo != null && usedDiskGo != null ? `${formatGo(usedDiskGo)} / ${formatGo(totalDiskGo)}` : undefined}
          colorClass="text-accent-green"
        />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="card p-5">
          <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Statut des VM</h3>
          <div className="divide-y divide-anthracite-600">
            {vmCounts.map(({ etat, count }) => (
              <div key={etat} className="flex items-center gap-3 py-2 text-sm">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: statusColor(etat) }} />
                <StatusBadge etat={etat} />
                <span className="ml-auto text-anthracite-100 font-medium">{count}</span>
                <span className="w-12 text-right text-xs text-anthracite-500">
                  {totalVms ? `${Math.round((count / totalVms) * 100)}%` : "--"}
                </span>
              </div>
            ))}
            {autreCount > 0 && (
              <div className="flex items-center gap-3 py-2 text-sm">
                <span className="h-2 w-2 shrink-0 rounded-full bg-anthracite-500" />
                <span className="text-anthracite-200 text-xs">Autre</span>
                <span className="ml-auto text-anthracite-100 font-medium">{autreCount}</span>
                <span className="w-12 text-right text-xs text-anthracite-500">
                  {totalVms ? `${Math.round((autreCount / totalVms) * 100)}%` : "--"}
                </span>
              </div>
            )}
            {totalVms === 0 && <div className="py-2 text-sm text-anthracite-500">Aucune VM pour l'instant.</div>}
          </div>
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-anthracite-100">Activité récente</h3>
            <button className="text-xs text-accent-blue hover:underline" onClick={() => navigateTo("datacenter", null, "activity")}>
              Voir tout →
            </button>
          </div>
          <div className="divide-y divide-anthracite-600">
            {recentTasks == null && <div className="py-2 text-sm text-anthracite-500">Chargement...</div>}
            {recentTasks && recentTasks.length === 0 && <div className="py-2 text-sm text-anthracite-500">Aucune activité récente.</div>}
            {recentTasks && recentTasks.map((t) => (
              <div key={t.id} className="flex items-center gap-3 py-2 text-sm">
                <StatusBadge etat={STATUT_ETAT[t.statut]} showLabel={false} />
                <span className="text-anthracite-100 truncate">{TASK_LABELS[t.type] || t.type}</span>
                <span className="text-anthracite-500 text-xs truncate">{t.cible}</span>
                <span className="ml-auto shrink-0 text-xs text-anthracite-500 font-mono">{formatHeure(t.cree_le)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Nœuds</h3>
        <div className="divide-y divide-anthracite-600">
          {enrichedNodes.map((n) => (
            <div key={n.id} className="flex items-center gap-3 py-2 text-sm">
              <StatusBadge etat={n.etat} showLabel={false} />
              <span className="text-anthracite-100 flex-1">{n.nom}</span>
              <span className="text-anthracite-400 text-xs">{n.cpu_utilisation != null ? `${Math.round(n.cpu_utilisation * 100)}% CPU` : "CPU n/a"}</span>
              <span className="text-anthracite-400 text-xs">{n.memoire_totale_mo != null ? `${formatMo(n.memoire_utilisee_mo)} / ${formatMo(n.memoire_totale_mo)}` : "RAM n/a"}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
