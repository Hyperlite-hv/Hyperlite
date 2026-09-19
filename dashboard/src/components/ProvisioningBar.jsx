import { Loader2 } from "lucide-react";

const PHASE_LABELS = {
  demarrage: "Starting the VM...",
  installation: "Unattended installation in progress (packages, configuration)...",
  arretee: "VM stopped before the installation finished: restart it to resume.",
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

// INDETERMINATE progress bar (no real percentage available on the backend, see
// GET /vms/{name}/provisioning) for an unattended ISO installation in progress:
// the only reliable signal is "does the SSH port answer", so there is no real
// progress measure, just "still running" vs "done".
export default function ProvisioningBar({ status }) {
  if (!status || !status.provisioning) return null;

  return (
    <div className="card p-5">
      <div className="flex items-center gap-2.5">
        <Loader2 size={16} className="animate-spin text-accent-blue shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-anthracite-100">
            Unattended installation {status.os_family ? `(${FAMILY_LABELS[status.os_family] || status.os_family})` : ""} in progress
          </div>
          <div className="text-xs text-anthracite-400 mt-0.5">
            {PHASE_LABELS[status.phase] || "In progress..."} {status.elapsed_s != null && `(${formatElapsed(status.elapsed_s)})`}
          </div>
        </div>
      </div>
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-anthracite-600">
        <div className="h-full w-1/3 rounded-full bg-accent-blue provisioning-indeterminate" />
      </div>
      <p className="mt-2 text-[11px] text-anthracite-400">
        The web SSH terminal will be available automatically as soon as the installation ends.
      </p>
    </div>
  );
}
