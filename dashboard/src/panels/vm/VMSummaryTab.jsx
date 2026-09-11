import { useState } from "react";
import { Play, Square, RotateCw, Trash2 } from "lucide-react";
import GaugeRing from "../../components/GaugeRing";
import MetricChart from "../../components/MetricChart";
import RangeToggle from "../../components/RangeToggle";
import ConfirmDialog from "../../components/ConfirmDialog";
import { useSimulatedMetrics } from "../../hooks/useSimulatedMetrics";
import { useTaskSimulator } from "../../hooks/useTaskSimulator";
import { useInfraStore } from "../../store/useInfraStore";
import { chartColors } from "../../theme/colors";
import { formatUptime, formatMo, formatGo, formatKbps } from "../../utils/format";

export default function VMSummaryTab({ resource: vm }) {
  const [range, setRange] = useState(1);
  const [confirm, setConfirm] = useState(null); // "stop-force" | "delete" | null
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const { simulateTask } = useTaskSimulator();

  const { data, current } = useSimulatedMetrics(vm?.nom, range, {
    cpu: 0.2 + Math.random() * 0.3,
    ram: vm ? vm.memoire_utilisee_mo / vm.memoire_mo : 0.4,
  });

  if (!vm) return null;
  const ramRatio = vm.memoire_utilisee_mo / vm.memoire_mo;
  const diskRatio = vm.disque_utilise_go / vm.disque_go;

  async function act(action) {
    try {
      const taskId = await runVMAction(vm.nom, action);
      simulateTask(taskId);
    } catch (e) {
      // erreur deja poussee en toast par le store
    }
  }

  return (
    <div className="space-y-5">
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
        <button className="btn-danger ml-auto" disabled={vm.etat === "actif"} onClick={() => setConfirm("delete")}>
          <Trash2 size={14} /> Supprimer
        </button>
      </div>

      <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
        <GaugeRing label="CPU" ratio={current.cpu} valueLabel={`${vm.vcpu} vCPU`} colorClass="text-accent-blue" />
        <GaugeRing label="RAM" ratio={ramRatio} valueLabel={`${formatMo(vm.memoire_utilisee_mo)} / ${formatMo(vm.memoire_mo)}`} colorClass="text-accent-orange" />
        <GaugeRing label="Stockage" ratio={diskRatio} valueLabel={`${formatGo(vm.disque_utilise_go)} / ${formatGo(vm.disque_go)}`} colorClass="text-accent-green" />
      </div>

      <div className="card p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-anthracite-100">CPU & RAM</h3>
          <RangeToggle value={range} onChange={setRange} />
        </div>
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
        <h3 className="text-sm font-semibold text-anthracite-100">Reseau (I/O)</h3>
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

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Statut</h3>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-anthracite-400 text-xs">Uptime</dt><dd className="text-anthracite-100">{formatUptime(vm.uptime_s)}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Adresse IP</dt><dd className="text-anthracite-100">{vm.ip || "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">OS detecte</dt><dd className="text-anthracite-100">{vm.os}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Utilisateur SSH</dt><dd className="text-anthracite-100">{vm.utilisateur_ssh || "inconnu"}</dd></div>
        </dl>
      </div>

      <ConfirmDialog
        open={confirm === "stop"}
        title={`Arreter '${vm.nom}' ?`}
        message="Un arret propre sera tente ; en cas d'echec, un arret force sera necessaire."
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
