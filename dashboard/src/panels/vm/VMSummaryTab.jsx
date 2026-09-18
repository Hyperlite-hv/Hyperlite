import { useEffect, useState } from "react";
import { Play, Square, Power, RotateCw, Trash2, Copy, Layers, ArrowRightLeft, ShieldCheck, ShieldOff, Timer } from "lucide-react";
import GaugeRing from "../../components/GaugeRing";
import MetricChart from "../../components/MetricChart";
import MetricsHistoryCard from "../../components/MetricsHistoryCard";
import { fetchVMMetricsHistory } from "../../api/client";
import ConfirmDialog from "../../components/ConfirmDialog";
import ProvisioningBar from "../../components/ProvisioningBar";
import { useLiveVMMetrics } from "../../hooks/useLiveVMMetrics";
import { useProvisioningStatus } from "../../hooks/useProvisioningStatus";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { chartColors } from "../../theme/colors";
import { formatUptime, formatMo, formatKbps } from "../../utils/format";
import {
  cloneVM, createTemplateFromVM, migrateVM, fetchHaProtected, enableHa, disableHa,
  fetchVMAutoCleanup, setVMAutoCleanup, disableVMAutoCleanup,
} from "../../api/client";

export default function VMSummaryTab({ resource: vm }) {
  const [confirm, setConfirm] = useState(null); // "stop" | "force-stop" | "delete" | null
  const [migrateOpen, setMigrateOpen] = useState(false);
  const [migrateTarget, setMigrateTarget] = useState("");
  const [migrating, setMigrating] = useState(false);
  const [haProtected, setHaProtected] = useState(false);
  const [haBusy, setHaBusy] = useState(false);
  const [autoCleanup, setAutoCleanup] = useState(null); // { active, inactive_days, ... } | null (chargement)
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupDays, setCleanupDays] = useState(7);
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const loadAll = useInfraStore((s) => s.loadAll);
  const select = useInfraStore((s) => s.select);
  const pushToast = useInfraStore((s) => s.pushToast);
  const nodes = useInfraStore((s) => s.nodes);
  const isAdmin = useAuthStore(selectIsAdmin);
  const { data, current, error } = useLiveVMMetrics(vm?.nom, vm?.etat === "actif");
  const { status: provStatus, justFinished } = useProvisioningStatus(vm?.nom, vm?.etat === "actif");

  useEffect(() => {
    if (justFinished) {
      pushToast({ kind: "success", title: "Installation terminée", message: `${vm.nom} : terminal SSH web disponible` });
      loadAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [justFinished]);

  // Chantier 12 : avant, un echec d'installation automatisee (timeout SSH,
  // VM disparue en cours de route) restait invisible -- la barre de
  // progression disparaissait juste silencieusement (provisioning: false
  // sans distinction succes/echec). GET /vms/{name}/provisioning renvoie
  // maintenant failed+erreur explicitement dans ce cas.
  useEffect(() => {
    if (provStatus?.failed) {
      pushToast({ kind: "error", title: "Échec de l'installation automatisée", message: provStatus.erreur || "Cause inconnue" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provStatus?.failed]);

  // Chantier 17 (HA) : verifie si CETTE VM est protegee -- GET /ha renvoie
  // toutes les VM protegees, pas de endpoint par-VM dedie (liste courte en
  // pratique, un filtre cote client suffit).
  useEffect(() => {
    if (!vm?.nom) return;
    let cancelled = false;
    fetchHaProtected()
      .then((rows) => { if (!cancelled) setHaProtected(rows.some((r) => r.vm_name === vm.nom)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [vm?.nom]);

  // Chantier 19 : recharge le statut a chaque changement de VM (pas de
  // liste globale comme la HA -- endpoint dedie par VM, voir
  // app/routers/vms.py::get_vm_auto_cleanup_route).
  useEffect(() => {
    if (!vm?.nom) return;
    let cancelled = false;
    fetchVMAutoCleanup(vm.nom)
      .then((c) => { if (!cancelled) { setAutoCleanup(c); if (c.active) setCleanupDays(c.inactive_days); } })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [vm?.nom]);

  async function handleSetCleanup() {
    setCleanupBusy(true);
    try {
      await setVMAutoCleanup(vm.nom, cleanupDays);
      pushToast({ kind: "success", title: "Nettoyage automatique activé", message: `${vm.nom} -- ${cleanupDays} jour(s) d'inactivité` });
      setAutoCleanup({ active: true, inactive_days: cleanupDays });
      setCleanupOpen(false);
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally {
      setCleanupBusy(false);
    }
  }

  async function handleDisableCleanup() {
    setCleanupBusy(true);
    try {
      await disableVMAutoCleanup(vm.nom);
      pushToast({ kind: "success", title: "Nettoyage automatique désactivé", message: vm.nom });
      setAutoCleanup({ active: false });
      setCleanupOpen(false);
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally {
      setCleanupBusy(false);
    }
  }

  async function handleToggleHa() {
    setHaBusy(true);
    try {
      if (haProtected) {
        await disableHa(vm.nom);
        pushToast({ kind: "success", title: "Protection HA désactivée", message: vm.nom });
        setHaProtected(false);
      } else {
        await enableHa(vm.nom, vm.node);
        pushToast({ kind: "success", title: "Protection HA activée", message: vm.nom });
        setHaProtected(true);
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Échec", message: e.message });
    } finally {
      setHaBusy(false);
    }
  }

  if (!vm) return null;
  const provisioning = provStatus?.provisioning;

  async function act(action, opts) {
    try {
      await runVMAction(vm.nom, action, opts);
      if (action === "delete") select("datacenter", null);
    } catch (e) {
      // erreur deja poussee en toast par le store
    }
  }

  async function handleClone() {
    const newName = window.prompt(`Nom de la copie de '${vm.nom}' :`, `${vm.nom}-clone`);
    if (!newName || !newName.trim()) return;
    try {
      await cloneVM(vm.nom, newName.trim());
      pushToast({ kind: "success", title: "VM clonée", message: `${vm.nom} -> ${newName.trim()}` });
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec du clonage", message: e.message });
    }
  }

  async function handleMigrate() {
    if (!migrateTarget) return;
    setMigrating(true);
    try {
      await migrateVM(vm.nom, migrateTarget, vm.node);
      pushToast({ kind: "success", title: "Migration lancée", message: `${vm.nom} vers ${migrateTarget} — suivez la progression dans les tâches` });
      setMigrateOpen(false);
      setMigrateTarget("");
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la migration", message: e.message });
    } finally {
      setMigrating(false);
    }
  }

  async function handleToTemplate() {
    const tplName = window.prompt(`Nom du template a creer depuis '${vm.nom}' :`, vm.nom);
    if (!tplName || !tplName.trim()) return;
    try {
      await createTemplateFromVM(vm.nom, tplName.trim());
      pushToast({ kind: "success", title: "Template créé", message: tplName.trim() });
      select("datacenter", null); // la VM source vient de disparaitre (convertie)
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Échec de la conversion", message: e.message });
    }
  }

  // "kvm-lab" comme destination (meme depuis un nœud DISTANT) n'est plus
  // exclu depuis le backlog 2026-09-18 : confiance SSH inverse etablie
  // automatiquement a l'enregistrement de chaque nœud (voir
  // app/core/cluster.py::ensure_reverse_trust).
  const migrationTargets = nodes.filter((n) => n.id !== vm.node && n.etat === "online");

  return (
    <div className="space-y-5">
      {isAdmin && (
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" disabled={vm.etat === "actif"} onClick={() => act("start")}>
            <Play size={14} /> Démarrer
          </button>
          <button className="btn-secondary" disabled={vm.etat !== "actif"} onClick={() => setConfirm("stop")}>
            <Square size={14} /> Arrêter
          </button>
          <button
            className="btn-secondary text-status-error"
            disabled={vm.etat !== "actif"}
            title="Coupe la VM immédiatement, sans attendre l'invite (équivalent à débrancher). À utiliser si l'arrêt propre ne répond pas."
            onClick={() => setConfirm("force-stop")}
          >
            <Power size={14} /> Forcer l'arrêt
          </button>
          <button className="btn-secondary" disabled={vm.etat !== "actif"} onClick={() => act("restart")}>
            <RotateCw size={14} /> Redémarrer
          </button>
          <button className="btn-secondary" disabled={vm.etat === "actif"} onClick={handleClone}>
            <Copy size={14} /> Cloner
          </button>
          <button className="btn-secondary" disabled={vm.etat === "actif"} onClick={handleToTemplate}>
            <Layers size={14} /> Vers template
          </button>
          <button
            className="btn-secondary"
            disabled={vm.etat !== "actif" || migrationTargets.length === 0}
            title={migrationTargets.length === 0 ? "Aucun autre nœud en ligne disponible" : "Migrer cette VM vers un autre nœud sans l'éteindre"}
            onClick={() => setMigrateOpen((o) => !o)}
          >
            <ArrowRightLeft size={14} /> Migrer
          </button>
          <button
            className={haProtected ? "btn-secondary text-status-running" : "btn-secondary"}
            disabled={haBusy}
            title={haProtected ? "Désactiver la protection HA (récupération manuelle en cas de panne du nœud)" : "Activer la protection HA -- exige un disque sur un pool de stockage partagé (chantier 26)"}
            onClick={handleToggleHa}
          >
            {haProtected ? <ShieldCheck size={14} /> : <ShieldOff size={14} />} {haBusy ? "..." : haProtected ? "Protégée HA" : "Protéger (HA)"}
          </button>
          <button
            className={autoCleanup?.active ? "btn-secondary text-status-warning" : "btn-secondary"}
            title="Supprime automatiquement cette VM après N jours d'arrêt continu (une VM en marche n'est jamais concernée)"
            onClick={() => setCleanupOpen((o) => !o)}
          >
            <Timer size={14} /> {autoCleanup?.active ? `Nettoyage auto (${autoCleanup.inactive_days}j)` : "Nettoyage auto"}
          </button>
          <button className="btn-danger ml-auto" disabled={vm.etat === "actif"} onClick={() => setConfirm("delete")}>
            <Trash2 size={14} /> Supprimer
          </button>
        </div>
      )}

      {migrateOpen && (
        <div className="card flex flex-wrap items-center gap-3 p-4">
          <span className="text-sm text-anthracite-200">Migrer <b className="text-anthracite-100">{vm.nom}</b> vers</span>
          <select className="input w-auto" value={migrateTarget} onChange={(e) => setMigrateTarget(e.target.value)}>
            <option value="">Choisir un nœud…</option>
            {migrationTargets.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
          </select>
          <button className="btn-primary" disabled={!migrateTarget || migrating} onClick={handleMigrate}>
            {migrating ? "Lancement..." : "Migrer"}
          </button>
          <button className="btn-secondary" onClick={() => setMigrateOpen(false)}>Annuler</button>
          <p className="w-full text-xs text-anthracite-400">
            Migration à chaud : la VM continue de tourner pendant le transfert. Si le disque n'est pas sur un pool partagé
            (chantier 26), il est copié pendant la migration — peut prendre du temps selon sa taille.
          </p>
        </div>
      )}

      {cleanupOpen && (
        <div className="card flex flex-wrap items-center gap-3 p-4">
          <span className="text-sm text-anthracite-200">Supprimer <b className="text-anthracite-100">{vm.nom}</b> après</span>
          <input
            type="number" min={1} max={365} className="input w-20"
            value={cleanupDays} onChange={(e) => setCleanupDays(Number(e.target.value))}
          />
          <span className="text-sm text-anthracite-200">jour(s) d'arrêt continu</span>
          <button className="btn-primary" disabled={cleanupBusy} onClick={handleSetCleanup}>
            {cleanupBusy ? "..." : autoCleanup?.active ? "Mettre à jour" : "Activer"}
          </button>
          {autoCleanup?.active && (
            <button className="btn-danger" disabled={cleanupBusy} onClick={handleDisableCleanup}>Désactiver</button>
          )}
          <button className="btn-secondary" onClick={() => setCleanupOpen(false)}>Fermer</button>
          <p className="w-full text-xs text-anthracite-400">
            Le compteur ne court que pendant que la VM est arrêtée (redémarrer la remet à zéro) et une VM protégée HA n'est jamais concernée.
            Une alerte est envoyée ~24h avant la suppression réelle.
          </p>
        </div>
      )}

      {vm.etat !== "actif" ? (
        <div className="card p-8 text-center text-sm text-anthracite-400">VM arrêtée -- pas de métriques en direct.</div>
      ) : provisioning ? (
        <ProvisioningBar status={provStatus} />
      ) : (
        <>
          <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
            <GaugeRing label="CPU" ratio={current?.cpu ?? 0} valueLabel={`${vm.vcpu} vCPU`} colorClass="text-accent-blue" />
            <GaugeRing
              label="RAM"
              ratio={current?.ram ?? 0}
              valueLabel={current ? `${formatMo(current.ramUseeMo)} / ${formatMo(current.ramAlloueeMo)}` : "--"}
              colorClass="text-accent-orange"
            />
            <div className="flex flex-col items-center justify-center gap-1 text-center">
              <div className="text-sm text-anthracite-300">
                {(current?.disques || []).map((d) => (
                  <div key={d.cible}>{d.cible} : {formatKbps(d.lecture_ko_s)} lu / {formatKbps(d.ecriture_ko_s)} écrit</div>
                ))}
              </div>
              <div className="text-xs text-anthracite-400 mt-1">Disques (débit instantané)</div>
            </div>
          </div>

          {error && <div className="text-xs text-status-error">Erreur de lecture des métriques : {error}</div>}

          <div className="card p-5">
            <h3 className="text-sm font-semibold text-anthracite-100">CPU & RAM (session en cours)</h3>
            <div className="mt-3">
              <MetricChart
                data={data}
                series={[
                  { key: "cpu", label: "CPU", color: chartColors.cpu },
                  { key: "ram", label: "RAM", color: chartColors.ram },
                ]}
                yFormatter={(v) => `${Math.round(v * 100)}%`}
              />
            </div>
          </div>

          <div className="card p-5">
            <h3 className="text-sm font-semibold text-anthracite-100">Réseau (Ko/s)</h3>
            <div className="mt-3">
              <MetricChart
                data={data}
                series={[
                  { key: "netIn", label: "Entrant", color: chartColors.netIn },
                  { key: "netOut", label: "Sortant", color: chartColors.netOut },
                ]}
                yFormatter={(v) => formatKbps(v)}
              />
            </div>
          </div>
        </>
      )}

      <MetricsHistoryCard title="Historique CPU (persisté)" fetcher={(range) => fetchVMMetricsHistory(vm.nom, range)} />

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Statut</h3>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-anthracite-400 text-xs">Uptime</dt><dd className="text-anthracite-100">{formatUptime(vm.uptime_s)}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Adresse IP</dt><dd className="text-anthracite-100">{vm.ip || "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">OS détecté</dt><dd className="text-anthracite-100">{vm.os || "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Utilisateur SSH</dt><dd className="text-anthracite-100">{vm.utilisateur_ssh || "inconnu"}</dd></div>
          <div>
            <dt className="text-anthracite-400 text-xs">Nettoyage automatique</dt>
            <dd className={autoCleanup?.active ? "text-status-warning" : "text-anthracite-100"}>
              {autoCleanup?.active ? `Actif -- ${autoCleanup.inactive_days}j d'arrêt` : "Inactif"}
            </dd>
          </div>
        </dl>
      </div>

      <ConfirmDialog
        open={confirm === "stop"}
        title={`Arrêter '${vm.nom}' ?`}
        message="Un arrêt propre (ACPI) sera tenté. Si l'invite ne répond pas (ex. écran figé), la VM restera active -- utilise 'Forcer l'arrêt' dans ce cas."
        confirmLabel="Arrêter"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("stop"); }}
      />
      <ConfirmDialog
        open={confirm === "force-stop"}
        title={`Forcer l'arrêt de '${vm.nom}' ?`}
        message="Coupe la VM immédiatement, comme si on débranchait l'alimentation -- pas d'arrêt propre du système, risque de perte de données non enregistrées. À n'utiliser que si l'arrêt normal ne fonctionne pas."
        confirmLabel="Forcer l'arrêt"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("stop", { force: true }); }}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        title={`Supprimer '${vm.nom}' ?`}
        message="Cette action est irréversible : la VM et son disque seront définitivement supprimés."
        confirmLabel="Supprimer"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("delete"); }}
      />
    </div>
  );
}
