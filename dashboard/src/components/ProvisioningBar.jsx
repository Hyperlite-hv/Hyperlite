import { Loader2 } from "lucide-react";

const PHASE_LABELS = {
  demarrage: "Démarrage de la VM...",
  installation: "Installation automatisée en cours (paquets, configuration)...",
  arretee: "VM arrêtée avant la fin de l'installation -- redémarrez-la pour reprendre.",
};

const FAMILY_LABELS = {
  kickstart: "Kickstart",
  autoinstall: "Autoinstall (Ubuntu)",
};

function formatElapsed(s) {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

// Barre de progression INDETERMINEE (pas de pourcentage reel disponible cote
// backend -- voir GET /vms/{name}/provisioning) pour une installation ISO
// automatisee en cours : le seul signal fiable est "le port SSH repond-il",
// donc pas de vraie mesure d'avancement, juste "toujours en cours" vs "fini".
export default function ProvisioningBar({ status }) {
  if (!status || !status.provisioning) return null;

  return (
    <div className="card p-5">
      <div className="flex items-center gap-2.5">
        <Loader2 size={16} className="animate-spin text-accent-blue shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-anthracite-100">
            Installation automatisée {status.os_family ? `(${FAMILY_LABELS[status.os_family] || status.os_family})` : ""} en cours
          </div>
          <div className="text-xs text-anthracite-400 mt-0.5">
            {PHASE_LABELS[status.phase] || "En cours..."} {status.elapsed_s != null && `(${formatElapsed(status.elapsed_s)})`}
          </div>
        </div>
      </div>
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-anthracite-600">
        <div className="h-full w-1/3 rounded-full bg-accent-blue provisioning-indeterminate" />
      </div>
      <p className="mt-2 text-[11px] text-anthracite-400">
        Le terminal SSH web sera disponible automatiquement dès la fin de l'installation.
      </p>
    </div>
  );
}
