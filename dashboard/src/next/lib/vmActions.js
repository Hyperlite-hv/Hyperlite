import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { errorMessage } from "./errors";

// Same store action (runVMAction) and same API calls as the legacy UI; the only additions are
// confirmations for Stop and Restart (Restart is a hard power cycle: the client always sends force=true).
export function useVmActions() {
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const pushToast = useInfraStore((s) => s.pushToast);

  async function run(vm, action) {
    try {
      if (action === "stop") {
        const ok = await confirmAction({ title: `Stop ${vm.nom}?`, message: "The guest receives a shutdown request (ACPI). Whether it stops depends on the guest cooperating; use Force stop if it does not.", confirmLabel: "Stop", danger: false });
        if (!ok) return;
        await runVMAction(vm.nom, "stop", { force: false });
      } else if (action === "restart") {
        const ok = await confirmAction({ title: `Restart ${vm.nom}?`, message: "Restart is a hard power cycle: the guest is powered off immediately, without a clean shutdown, then started again.", confirmLabel: "Restart", danger: true });
        if (!ok) return;
        await runVMAction(vm.nom, "restart");
      } else {
        await runVMAction(vm.nom, action);
      }
    } catch (e) {
      pushToast({ kind: "error", title: `${action} failed`, message: errorMessage(e) });
    }
  }

  function openConsole(vm) {
    window.open(`/console/${encodeURIComponent(vm.nom)}`, `hl-console-${vm.nom}`, "width=1100,height=760");
  }

  return { run, openConsole };
}
