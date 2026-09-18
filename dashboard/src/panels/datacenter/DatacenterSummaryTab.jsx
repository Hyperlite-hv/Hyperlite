import { useEffect, useState } from "react";
import { Server, MonitorPlay, Square, AlertTriangle, Cpu, MemoryStick, Network, Clock, Plus } from "lucide-react";
import StatTile from "../../components/StatTile";
import StatusBadge from "../../components/StatusBadge";
import UsageBar from "../../components/UsageBar";
import MetricChart from "../../components/MetricChart";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchHostMetricsHistory, fetchTasks } from "../../api/client";
import { formatMo, formatUptime, formatKbps } from "../../utils/format";
import { statusColor, chartColors } from "../../theme/colors";

function sumDefined(items, key) {
  const defined = items.filter((i) => i[key] != null);
  if (!defined.length) return null;
  return defined.reduce((a, i) => a + i[key], 0);
}

// Etats reels d'un domaine libvirt (STATE_NAMES, app/routers/vms.py) dans
// l'ordre d'affichage voulu -- "bloque"/"en_arret"/"inconnu" sont regroupes
// dans "Autre" plus bas (transitoires/rares, pas la peine d'une ligne dediee
// tant qu'aucune VM n'y est).
const VM_STATUS_ORDER = [
  { etat: "actif", label: "En marche" },
  { etat: "arrete", label: "Arrêtées" },
  { etat: "en_pause", label: "En pause" },
  { etat: "suspendu", label: "Suspendues" },
  { etat: "plante", label: "En erreur" },
];

const TASK_LABELS = {
  create_vm: "Créer VM", delete_vm: "Supprimer VM", start_vm: "Démarrer VM",
  stop_vm: "Arrêter VM", restart_vm: "Redémarrer VM", clone_vm: "Cloner VM", migrate_vm: "Migrer VM",
  auto_install: "Installation automatisée", create_snapshot: "Créer snapshot",
  backup_vm: "Sauvegarder VM", restore_backup: "Restaurer sauvegarde",
  export_vm: "Exporter VM", upload_vm_disk: "Téléverser disque",
  create_container: "Créer conteneur", upload_iso: "Téléverser ISO",
  hyperlite_update: "Mise à jour Hyperlite", run_job: "Job d'automatisation",
};
const STATUT_ETAT = { en_cours: "avertissement", termine: "actif", echec: "erreur", en_attente: "avertissement" };
const STATUT_LABEL = { en_cours: "En cours", termine: "OK", echec: "Échec", en_attente: "En attente" };

// Seuil d'alerte identique au backend (app/core/metrics.py::ALERT_THRESHOLDS)
// -- calcule ici cote client a partir des memes donnees deja chargees
// (aucun endpoint /alerts dedie aujourd'hui, le backend se contente de logger
// le franchissement dans l'audit). Reste honnete : uniquement les nœuds pour
// lesquels on a une vraie mesure (voir enrichedNodes) comptent.
const ALERT_THRESHOLD = 0.9;

function formatHeure(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// CPU/RAM/reseau reels du noeud local ("local") via GET /host/metrics/history
// (collecte continue, chantier 10) -- fetchNodes() (api/client.js) pose
// volontairement ces champs a null (pas expose par GET /dashboard), ce qui
// affichait "n/a" en permanence ici meme apres le chantier 10. Meme source
// que NodeSummaryTab.jsx. Un seul noeud reel aujourd'hui (multi-noeuds
// distants, chantier 15, n'exposent pas encore leurs propres metriques) :
// les noeuds distants sans donnee restent "n/a", ils ne comptent simplement
// pas dans la moyenne/somme (sumDefined filtre deja les null).
export default function DatacenterSummaryTab() {
  const { nodes, vms, navigateTo } = useInfraStore((s) => ({ nodes: s.nodes, vms: s.vms, navigateTo: s.navigateTo }));
  const [metricRows, setMetricRows] = useState(null);
  const [recentTasks, setRecentTasks] = useState(null);

  useEffect(() => {
    const load = () => fetchHostMetricsHistory("1h").then(setMetricRows).catch(() => {});
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

  const latest = metricRows && metricRows.length ? metricRows[metricRows.length - 1] : null;
  const chartData = (metricRows || []).map((r) => ({
    t: new Date(r.ts).getTime(),
    cpu: (r.cpu_pct ?? 0) / 100,
    ramMo: r.mem_used_mb,
    ramTotalMo: r.mem_total_mb,
    netIn: (r.net_rx_bps ?? 0) / 1024,
    netOut: (r.net_tx_bps ?? 0) / 1024,
  }));

  // Fusionne les vraies metriques locales dans la liste des noeuds (le
  // noeud local est toujours id "local", voir fetchNodes()) avant tout
  // calcul -- une seule source de verite pour l'agregat ET le detail
  // par-noeud plus bas.
  const enrichedNodes = nodes.map((n) => (n.id !== "local" || !latest ? n : {
    ...n,
    cpu_utilisation: latest.cpu_pct != null ? latest.cpu_pct / 100 : n.cpu_utilisation,
    memoire_totale_mo: latest.mem_total_mb ?? n.memoire_totale_mo,
    memoire_utilisee_mo: latest.mem_used_mb ?? n.memoire_utilisee_mo,
  }));

  const totalRamMo = sumDefined(enrichedNodes, "memoire_totale_mo");
  const usedRamMo = sumDefined(enrichedNodes, "memoire_utilisee_mo");
  const cpuNodes = enrichedNodes.filter((n) => n.cpu_utilisation != null);
  const avgCpu = cpuNodes.length ? cpuNodes.reduce((a, n) => a + n.cpu_utilisation, 0) / cpuNodes.length : null;

  const totalVms = vms.length;
  const vmCounts = VM_STATUS_ORDER.map(({ etat, label }) => ({ etat, label, count: vms.filter((v) => v.etat === etat).length }));
  const autreCount = totalVms - vmCounts.reduce((a, c) => a + c.count, 0);
  const runningVms = vmCounts.find((c) => c.etat === "actif")?.count ?? 0;
  const stoppedVms = vmCounts.find((c) => c.etat === "arrete")?.count ?? 0;

  const onlineNodes = enrichedNodes.filter((n) => n.etat === "online").length;
  const alertCount = enrichedNodes.filter((n) => {
    const cpuAlert = n.cpu_utilisation != null && n.cpu_utilisation >= ALERT_THRESHOLD;
    const ramAlert = n.memoire_totale_mo && n.memoire_utilisee_mo != null && n.memoire_utilisee_mo / n.memoire_totale_mo >= ALERT_THRESHOLD;
    return cpuAlert || ramAlert;
  }).length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile icon={Server} label="Nœuds" value={enrichedNodes.length} foot={`${onlineNodes} en ligne`} tone="blue" />
        <StatTile icon={MonitorPlay} label="VM en marche" value={runningVms} foot={`sur ${totalVms} au total`} tone="green" />
        <StatTile icon={Square} label="VM arrêtées" value={stoppedVms} foot={`sur ${totalVms} au total`} tone="gray" />
        <StatTile icon={AlertTriangle} label="Alertes" value={alertCount} foot={alertCount ? "à surveiller" : "aucune"} tone="amber" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="card p-4">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-bold text-anthracite-100">
            <Cpu size={16} className="text-accent-blue" /> Utilisation CPU
          </div>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-extrabold text-accent-blue">
              {avgCpu != null ? `${Math.round(avgCpu * 100)}%` : "n/a"}
            </span>
            <span className="text-[11px] text-anthracite-400">{cpuNodes.length} nœud(s) mesuré(s)</span>
          </div>
          {chartData.length > 0 ? (
            <MetricChart data={chartData} series={[{ key: "cpu", label: "CPU", color: chartColors.cpu }]} yFormatter={(v) => `${Math.round(v * 100)}%`} height={140} />
          ) : (
            <div className="flex h-[140px] items-center justify-center text-xs text-anthracite-400">Pas encore d'historique.</div>
          )}
        </div>

        <div className="card p-4">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-bold text-anthracite-100">
            <MemoryStick size={16} className="text-accent-blue" /> Utilisation mémoire
          </div>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-extrabold text-accent-blue">
              {totalRamMo != null && usedRamMo != null ? `${Math.round((usedRamMo / totalRamMo) * 100)}%` : "n/a"}
            </span>
            <span className="text-[11px] text-anthracite-400">
              {totalRamMo != null && usedRamMo != null ? `${formatMo(usedRamMo)} / ${formatMo(totalRamMo)}` : ""}
            </span>
          </div>
          {chartData.length > 0 ? (
            <MetricChart data={chartData} series={[{ key: "ramMo", label: "RAM", color: chartColors.cpu }]} yFormatter={(v) => formatMo(v)} height={140} />
          ) : (
            <div className="flex h-[140px] items-center justify-center text-xs text-anthracite-400">Pas encore d'historique.</div>
          )}
        </div>

        <div className="card p-4">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-bold text-anthracite-100">
            <Network size={16} className="text-accent-blue" /> Réseau (hôte local)
          </div>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-extrabold text-accent-blue">
              {latest ? formatKbps((latest.net_rx_bps ?? 0) / 1024) : "n/a"}
            </span>
            <span className="text-[11px] text-anthracite-400">entrant actuel</span>
          </div>
          {chartData.length > 0 ? (
            <MetricChart
              data={chartData}
              series={[{ key: "netIn", label: "Entrant", color: chartColors.cpu }, { key: "netOut", label: "Sortant", color: chartColors.netOut }]}
              yFormatter={(v) => formatKbps(v)} height={140}
            />
          ) : (
            <div className="flex h-[140px] items-center justify-center text-xs text-anthracite-400">Pas encore d'historique.</div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <div className="card p-4 xl:col-span-7">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-[13px] font-bold text-anthracite-100">
              <Server size={16} className="text-accent-blue" /> Nœuds
            </h3>
            <button className="btn-primary" onClick={() => navigateTo("datacenter", null, "nodes")}>
              <Plus size={14} /> Ajouter un nœud
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10.5px] font-bold uppercase tracking-wide text-anthracite-400">
                  <th className="pb-2 pr-2">Nom</th>
                  <th className="pb-2 pr-2">Statut</th>
                  <th className="pb-2 pr-2">CPU</th>
                  <th className="pb-2 pr-2">Mémoire</th>
                  <th className="pb-2 pr-2">Disque</th>
                  <th className="pb-2 pr-2 text-right">VM</th>
                  <th className="pb-2 text-right">Actif depuis</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-anthracite-600">
                {enrichedNodes.map((n) => {
                  const ramPct = n.memoire_totale_mo ? (n.memoire_utilisee_mo / n.memoire_totale_mo) * 100 : null;
                  const diskPct = n.stockage_total_go ? (n.stockage_utilise_go / n.stockage_total_go) * 100 : null;
                  const vmTotal = (n.vms_actives ?? 0) + (n.vms_arretees ?? 0);
                  return (
                    <tr key={n.id} className="cursor-pointer hover:bg-anthracite-900" onClick={() => navigateTo("node", n.id, "summary")}>
                      <td className="py-2.5 pr-2">
                        <div className="flex items-center gap-2.5">
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-blue/10 text-accent-blue"><Server size={15} /></span>
                          <div className="min-w-0">
                            <div className="truncate font-semibold text-anthracite-100">{n.nom}</div>
                            <div className="truncate font-mono text-[10.5px] text-anthracite-400">{n.ip || "--"}</div>
                          </div>
                        </div>
                      </td>
                      <td className="py-2.5 pr-2"><StatusBadge etat={n.etat} /></td>
                      <td className="py-2.5 pr-2"><UsageBar pct={n.cpu_utilisation != null ? n.cpu_utilisation * 100 : null} color={chartColors.cpu} /></td>
                      <td className="py-2.5 pr-2"><UsageBar pct={ramPct} color={statusColor("actif")} /></td>
                      <td className="py-2.5 pr-2"><UsageBar pct={diskPct} color={statusColor("avertissement")} /></td>
                      <td className="py-2.5 pr-2 text-right font-mono text-xs text-anthracite-300">{n.vms_actives ?? 0} / {vmTotal}</td>
                      <td className="py-2.5 text-right font-mono text-xs text-anthracite-300">{n.uptime_s ? formatUptime(n.uptime_s) : "n/a"}</td>
                    </tr>
                  );
                })}
                {enrichedNodes.length === 0 && (
                  <tr><td colSpan={7} className="py-4 text-center text-sm text-anthracite-400">Aucun nœud.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card p-4 xl:col-span-5">
          <h3 className="mb-3 text-[13px] font-bold text-anthracite-100">Statut des VM</h3>
          <div className="divide-y divide-anthracite-600">
            {vmCounts.map(({ etat, label, count }) => (
              <div key={etat} className="flex items-center gap-3 py-2.5 text-sm">
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: statusColor(etat) }} />
                <span className="flex-1 font-semibold text-anthracite-200">{label}</span>
                <span className="font-mono text-[14px] font-extrabold text-anthracite-100">{count}</span>
                <span className="w-14 text-right font-mono text-xs text-anthracite-400">
                  {totalVms ? `${((count / totalVms) * 100).toFixed(1)}%` : "--"}
                </span>
              </div>
            ))}
            {autreCount > 0 && (
              <div className="flex items-center gap-3 py-2.5 text-sm">
                <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-anthracite-500" />
                <span className="flex-1 font-semibold text-anthracite-200">Autre</span>
                <span className="font-mono text-[14px] font-extrabold text-anthracite-100">{autreCount}</span>
                <span className="w-14 text-right font-mono text-xs text-anthracite-400">
                  {totalVms ? `${((autreCount / totalVms) * 100).toFixed(1)}%` : "--"}
                </span>
              </div>
            )}
            {totalVms === 0 && <div className="py-2 text-sm text-anthracite-400">Aucune VM pour l'instant.</div>}
          </div>
          {totalVms > 0 && (
            <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-anthracite-900">
              {vmCounts.filter((c) => c.count > 0).map(({ etat, count }) => (
                <div key={etat} style={{ width: `${(count / totalVms) * 100}%`, backgroundColor: statusColor(etat) }} />
              ))}
              {autreCount > 0 && <div style={{ width: `${(autreCount / totalVms) * 100}%` }} className="bg-anthracite-500" />}
            </div>
          )}
        </div>
      </div>

      <div className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-anthracite-100">
            <Clock size={16} className="text-accent-blue" /> Tâches récentes
          </h3>
          <button className="text-xs font-semibold text-accent-blue hover:underline" onClick={() => navigateTo("datacenter", null, "activity")}>
            Voir tout le journal →
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10.5px] font-bold uppercase tracking-wide text-anthracite-400">
                <th className="pb-2 pr-2">Heure</th>
                <th className="pb-2 pr-2">Nœud</th>
                <th className="pb-2 pr-2">Utilisateur</th>
                <th className="pb-2 pr-2">Tâche</th>
                <th className="pb-2">Statut</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-anthracite-600">
              {recentTasks == null && (
                <tr><td colSpan={5} className="py-3 text-sm text-anthracite-400">Chargement...</td></tr>
              )}
              {recentTasks && recentTasks.length === 0 && (
                <tr><td colSpan={5} className="py-3 text-sm text-anthracite-400">Aucune activité récente.</td></tr>
              )}
              {recentTasks && recentTasks.map((t) => (
                <tr key={t.id}>
                  <td className="py-2.5 pr-2 font-mono text-xs text-anthracite-300">{formatHeure(t.cree_le)}</td>
                  <td className="py-2.5 pr-2 text-anthracite-200">{t.node || "local"}</td>
                  <td className="py-2.5 pr-2 text-anthracite-200">{t.username || "--"}</td>
                  <td className="py-2.5 pr-2 text-anthracite-100">
                    {TASK_LABELS[t.type] || t.type}
                    {t.cible && <span className="text-anthracite-400"> — {t.cible}</span>}
                  </td>
                  <td className="py-2.5">
                    <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-bold" style={{
                      backgroundColor: `${statusColor(STATUT_ETAT[t.statut])}1A`, color: statusColor(STATUT_ETAT[t.statut]),
                    }}>
                      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: statusColor(STATUT_ETAT[t.statut]) }} />
                      {STATUT_LABEL[t.statut] || t.statut}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
