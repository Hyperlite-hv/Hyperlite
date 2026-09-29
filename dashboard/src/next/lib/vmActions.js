import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { errorMessage } from "./errors";
import { apiNode, vmKey } from "./vmId";
import { useT } from "../i18n";

const ACTION_LABEL_KEY = { start: "menu.start", stop: "confirm.stop.confirm", "force-stop": "confirm.forceStop.confirm", restart: "confirm.restart.confirm" };

// Same store action (runVMAction) and same API calls as the legacy UI; the only additions are
// confirmations for Stop and Restart (Restart is a hard power cycle: the client always sends force=true).
export function useVmActions() {
  const t = useT();
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const pushToast = useInfraStore((s) => s.pushToast);

  async function run(vm, action) {
    try {
      if (action === "stop") {
        const ok = await confirmAction({ title: t("confirm.stop.title", { name: vm.nom }), message: t("confirm.stop.message"), confirmLabel: t("confirm.stop.confirm"), danger: false });
        if (!ok) return;
        await runVMAction(vmKey(vm), "stop", { force: false });
      } else if (action === "force-stop") {
        const ok = await confirmAction({ title: t("confirm.forceStop.title", { name: vm.nom }), message: t("confirm.forceStop.message"), confirmLabel: t("confirm.forceStop.confirm"), danger: true });
        if (!ok) return;
        await runVMAction(vmKey(vm), "stop", { force: true });
      } else if (action === "restart") {
        const ok = await confirmAction({ title: t("confirm.restart.title", { name: vm.nom }), message: t("confirm.restart.message"), confirmLabel: t("confirm.restart.confirm"), danger: true });
        if (!ok) return;
        await runVMAction(vmKey(vm), "restart");
      } else {
        await runVMAction(vmKey(vm), action);
      }
    } catch (e) {
      const label = ACTION_LABEL_KEY[action] ? t(ACTION_LABEL_KEY[action]) : action;
      pushToast({ kind: "error", title: t("action.failed", { action: label }), message: errorMessage(e, t("err.unknown")) });
    }
  }

  function openConsole(vm) {
    window.open(consoleUrl(vm), `hl-console-${vmKey(vm)}`, "width=1100,height=760,noopener");
  }

  return { run, openConsole };
}

// The console window of a VM: its node travels in the URL, so a remote VM's console is never looked up on the local
// host (nor a homonymous local VM's shown instead).
export function consoleUrl(vm, mode) {
  const params = new URLSearchParams();
  if (apiNode(vm.node)) params.set("node", vm.node);
  if (mode) params.set("mode", mode);
  const qs = params.toString();
  return `/console/${encodeURIComponent(vm.nom)}${qs ? `?${qs}` : ""}`;
}
