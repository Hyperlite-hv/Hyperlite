import { useEffect, useState } from "react";
import { X, ChevronLeft, ChevronRight, Check } from "lucide-react";
import StepNode from "./steps/StepNode";
import StepTemplate from "./steps/StepTemplate";
import StepResources from "./steps/StepResources";
import StepNetwork from "./steps/StepNetwork";
import StepReview from "./steps/StepReview";
import { useInfraStore } from "../store/useInfraStore";
import { createVM, fetchHostProfile } from "../api/client";
import { detectOsFamily } from "../utils/osFamily";

const STEPS = [
  { id: "node", label: "Nœud", Component: StepNode },
  { id: "template", label: "Modèle", Component: StepTemplate },
  { id: "resources", label: "CPU / RAM / Disque", Component: StepResources },
  { id: "network", label: "Réseau", Component: StepNetwork },
  { id: "review", label: "Résumé", Component: StepReview },
];

function initialForm(nodes, networks, defaults) {
  return {
    node: nodes[0]?.id || "",
    iso: "",
    importDisk: null,
    name: "",
    // Valeurs par defaut du PROFIL de deploiement (chantier 5, GET
    // /host/profile), deja bornees par les limites reelles de l'hote ;
    // repli sur les valeurs historiques si l'appel echoue.
    vcpu: defaults?.vcpu ?? 1,
    memory_mb: defaults?.memory_mb ?? 1024,
    disks: [{ size_gb: defaults?.disk_gb ?? 10 }],
    username: "",
    password: "",
    network: networks[0]?.nom || "default",
    // Choix du pool de stockage (backlog 2026-09-18) : vide = pool
    // 'default', comportement historique inchangé.
    storagePool: "",
    autoCleanupEnabled: false,
    autoCleanupDays: 7,
  };
}

export default function VMWizard({ open, onClose }) {
  const nodes = useInfraStore((s) => s.nodes);
  const networks = useInfraStore((s) => s.networks);
  const storagePools = useInfraStore((s) => s.storagePools);
  const addTask = useInfraStore((s) => s.addTask);
  const completeTask = useInfraStore((s) => s.completeTask);
  const loadAll = useInfraStore((s) => s.loadAll);
  const pushToast = useInfraStore((s) => s.pushToast);

  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState(() => initialForm(nodes, networks));
  const [profileDefaults, setProfileDefaults] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    fetchHostProfile().then((p) => {
      if (!alive) return;
      const d = p.vm_defaults_effectifs;
      setProfileDefaults(d);
      // N'ecrase que des ressources encore intactes (valeurs historiques).
      setForm((f) => (f.vcpu === 1 && f.memory_mb === 1024 && f.disks.length === 1 && f.disks[0].size_gb === 10
        ? { ...f, vcpu: d.vcpu, memory_mb: d.memory_mb, disks: [{ size_gb: d.disk_gb }] } : f));
    }).catch(() => {});
    return () => { alive = false; };
  }, [open]);

  if (!open) return null;

  function patch(fields) {
    setForm((f) => ({ ...f, ...fields }));
  }

  function reset() {
    setStepIndex(0);
    setForm(initialForm(nodes, networks, profileDefaults));
  }

  async function handleCreate() {
    // Meme forme de payload que POST /vms cote backend reel (app/routers/vms.py
    // VMCreate) : name, vcpu, memory_mb, disks[], network, username, password, iso.
    const payload = {
      name: form.name, vcpu: form.vcpu, memory_mb: form.memory_mb,
      disks: form.disks, network: form.network, username: form.username,
      password: form.password, iso: form.iso || null,
      import_disk: form.importDisk || null,
      storage_pool: form.storagePool || null,
      auto_cleanup_days: form.autoCleanupEnabled ? form.autoCleanupDays : null,
    };
    const taskId = addTask({ type: "create_vm", cible: form.name, node: form.node });
    try {
      await createVM(payload);
      completeTask(taskId, "termine");
      await loadAll(); // recharge depuis le backend plutot que de deviner l'etat cree
      onClose();
      reset();
    } catch (e) {
      completeTask(taskId, "echec", e.message);
    }
  }

  const Step = STEPS[stepIndex].Component;
  const isLast = stepIndex === STEPS.length - 1;
  // Etape "resources" (index 2) : nom toujours requis ; utilisateur/mot de
  // passe requis sauf en installation manuelle (ISO non reconnu, voir
  // StepTemplate/StepResources/detectOsFamily) -- sans ISO (cloud-init) ou
  // avec un ISO reconnu (installation automatisee), le compte est bien cree
  // par Hyperlite, donc toujours requis ici.
  const manualInstall = Boolean(form.iso) && !detectOsFamily(form.iso);
  const importMode = form.importDisk != null;
  const canNext = stepIndex !== 2 || (form.name && (importMode ? Boolean(form.importDisk) : (manualInstall || (form.username && form.password.length >= 4))));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-2xl overflow-hidden">
        <div className="flex items-center justify-between border-b border-anthracite-600 px-5 py-3">
          <h2 className="text-sm font-semibold text-anthracite-100">Créer une machine virtuelle</h2>
          <button onClick={() => { onClose(); reset(); }} className="text-anthracite-400 hover:text-anthracite-100"><X size={16} /></button>
        </div>

        <div className="flex gap-1 px-5 pt-3">
          {STEPS.map((s, i) => (
            <div key={s.id} className={`flex-1 h-1 rounded-full ${i <= stepIndex ? "bg-accent-blue" : "bg-anthracite-600"}`} />
          ))}
        </div>
        <div className="flex justify-between px-5 pt-1.5 pb-3">
          {STEPS.map((s, i) => (
            <span key={s.id} className={`text-[11px] ${i === stepIndex ? "text-anthracite-100 font-medium" : "text-anthracite-500"}`}>{s.label}</span>
          ))}
        </div>

        <div className="max-h-[55vh] overflow-y-auto px-5 py-2">
          <Step form={form} patch={patch} nodes={nodes} networks={networks} storagePools={storagePools} />
        </div>

        <div className="flex justify-between border-t border-anthracite-600 px-5 py-3">
          <button className="btn-secondary" disabled={stepIndex === 0} onClick={() => setStepIndex((i) => i - 1)}>
            <ChevronLeft size={14} /> Précédent
          </button>
          {isLast ? (
            <button className="btn-primary" onClick={handleCreate}>
              <Check size={14} /> Créer la VM
            </button>
          ) : (
            <button className="btn-primary" disabled={!canNext} onClick={() => setStepIndex((i) => i + 1)}>
              Suivant <ChevronRight size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
