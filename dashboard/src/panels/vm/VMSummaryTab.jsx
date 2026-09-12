import { useEffect, useState } from "react";
import { Play, Square, RotateCw, Trash2, Copy, Layers } from "lucide-react";
import GaugeRing from "../../components/GaugeRing";
import MetricChart from "../../components/MetricChart";
import ConfirmDialog from "../../components/ConfirmDialog";
import ProvisioningBar from "../../components/ProvisioningBar";
import { useLiveVMMetrics } from "../../hooks/useLiveVMMetrics";
import { useProvisioningStatus } from "../../hooks/useProvisioningStatus";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { chartColors } from "../../theme/colors";
import { formatUptime, formatMo, formatKbps } from "../../utils/format";
import { cloneVM, createTemplateFromVM } from "../../api/client";

export default function VMSummaryTab({ resource: vm }) {
  const [confirm, setConfirm] = useState(null); // "stop" | "delete" | null
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const loadAll = useInfraStore((s) => s.loadAll);
  const select = useInfraStore((s) => s.select);
  const pushToast = useInfraStore((s) => s.pushToast);
  const isAdmin = useAuthStore(selectIsAdmin);
  const { data, current, error } = useLiveVMMetrics(vm?.nom, vm?.etat === "actif");
  const { status: provStatus, justFinished } = useProvisioningStatus(vm?.nom, vm?.etat === "actif");

  useEffect(() => {
    if (justFinished) {
      pushToast({ kind: "success", title: "Installation terminee", message: `${vm.nom} : terminal SSH web disponible` });
      loadAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [justFinished]);

  if (!vm) return null;
  const provisioning = provStatus?.provisioning;

  async function act(action) {
    try {
      await runVMAction(vm.nom, action);
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
      pushToast({ kind: "success", title: "VM clonee", message: `${vm.nom} -> ${newName.trim()}` });
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Echec du clonage", message: e.message });
    }
  }

  async function handleToTemplate() {
    const tplName = window.prompt(`Nom du template a creer depuis '${vm.nom}' :`, vm.nom);
    if (!tplName || !tplName.trim()) return;
    try {
      await createTemplateFromVM(vm.nom, tplName.trim());
      pushToast({ kind: "success", title: "Template cree", message: tplName.trim() });
      select("datacenter", null); // la VM source vient de disparaitre (convertie)
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Echec de la conversion", message: e.message });
    }
  }

  return (
    <div className="space-y-5">
      {isAdmin && (
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" disabled={vm.etat === "actif"} onClick={() => act("start")}>
            <Play size={14} /> Demarrer
          </button>
          <button className="btn-secondary" disabled={vm.etat !== "actif"} onClick={() => setConfirm("stop")}>
            <Square size={14} /> Arreter
          </button>
          <button className="btn-secondary" disabled={vm.etat !== "actif"} onClick={() => act("restart")}>
            <RotateCw size={14} /> Redemarrer
          </button>
          <button className="btn-secondary" disabled={vm.etat === "actif"} onClick={handleClone}>
            <Copy size={14} /> Cloner
          </button>
          <button className="btn-secondary" disabled={vm.etat === "actif"} onClick={handleToTemplate}>
            <Layers size={14} /> Vers template
          </button>
          <button className="btn-danger ml-auto" disabled={vm.etat === "actif"} onClick={() => setConfirm("delete")}>
            <Trash2 size={14} /> Supprimer
          </button>
        </div>
      )}

      {vm.etat !== "actif" ? (
        <div className="card p-8 text-center text-sm text-anthracite-400">VM arretee -- pas de metriques en direct.</div>
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
                  <div key={d.cible}>{d.cible} : {formatKbps(d.lecture_ko_s)} lu / {formatKbps(d.ecriture_ko_s)} ecrit</div>
                ))}
              </div>
              <div className="text-xs text-anthracite-400 mt-1">Disques (debit instantane)</div>
            </div>
          </div>

          {error && <div className="text-xs text-status-error">Erreur de lecture des metriques : {error}</div>}

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
            <h3 className="text-sm font-semibold text-anthracite-100">Reseau (Ko/s)</h3>
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

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Statut</h3>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-anthracite-400 text-xs">Uptime</dt><dd className="text-anthracite-100">{formatUptime(vm.uptime_s)}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Adresse IP</dt><dd className="text-anthracite-100">{vm.ip || "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">OS detecte</dt><dd className="text-anthracite-100">{vm.os || "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Utilisateur SSH</dt><dd className="text-anthracite-100">{vm.utilisateur_ssh || "inconnu"}</dd></div>
        </dl>
      </div>

      <ConfirmDialog
        open={confirm === "stop"}
        title={`Arreter '${vm.nom}' ?`}
        message="Un arret propre (ACPI) sera tente."
        confirmLabel="Arreter"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("stop"); }}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        title={`Supprimer '${vm.nom}' ?`}
        message="Cette action est irreversible : la VM et son disque seront definitivement supprimes."
        confirmLabel="Supprimer"
        onCancel={() => setConfirm(null)}
        onConfirm={() => { setConfirm(null); act("delete"); }}
      />
    </div>
  );
}
