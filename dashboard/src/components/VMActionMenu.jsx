import { useInfraStore } from "../store/useInfraStore";
import { exportVM } from "../api/client";
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuShortcut,
} from "@/components/ui/dropdown-menu";

// Actions menu for a VM: the same entries as the ⌘K palette (SearchBar), reachable
// from a dedicated trigger (the "More actions" button) instead of forcing users to
// know the shortcuts. ⌘K stays a convenience, not a prerequisite. `children` is the
// trigger element (wrapped with asChild, so it can be the exact "..." button
// already in VMCard/VMTable without changing its markup).
export default function VMActionMenu({ vm, children }) {
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const pushToast = useInfraStore((s) => s.pushToast);
  const active = vm.etat === "actif";

  function doExport() {
    exportVM(vm.nom)
      .then(() => pushToast({ kind: "success", title: "Export started", message: `${vm.nom}: available in Datacenter › Exports once finished` }))
      .catch((e) => pushToast({ kind: "error", title: "Export failed", message: e.message }));
  }
  function go(tab) {
    navigateTo("vm", vm.nom, tab);
  }
  function run(action, opts) {
    runVMAction(vm.nom, action, opts).catch(() => {});
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="font-mono text-[10px] font-normal text-muted-foreground">
          {vm.vcpu} vCPU · {vm.memoire_mo} MB
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {active ? (
          <>
            <DropdownMenuItem onClick={() => go("console")}>Open the console<DropdownMenuShortcut>C</DropdownMenuShortcut></DropdownMenuItem>
            <DropdownMenuItem onClick={() => go("snapshots")}>Create a snapshot<DropdownMenuShortcut>S</DropdownMenuShortcut></DropdownMenuItem>
            <DropdownMenuItem onClick={() => go("hardware")}>Edit hardware</DropdownMenuItem>
            <DropdownMenuItem onClick={() => go("options")}>Clone</DropdownMenuItem>
            <DropdownMenuItem onClick={doExport}>Export the disk</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => run("restart")}>Restart</DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onClick={() => run("stop")}>Stop</DropdownMenuItem>
          </>
        ) : (
          <>
            <DropdownMenuItem onClick={() => run("start")}>Start</DropdownMenuItem>
            <DropdownMenuItem onClick={() => go("hardware")}>Edit hardware</DropdownMenuItem>
            <DropdownMenuItem onClick={() => go("options")}>Clone</DropdownMenuItem>
            <DropdownMenuItem onClick={doExport}>Export the disk</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => go("summary")}>Delete…</DropdownMenuItem>
          </>
        )}
        <DropdownMenuSeparator />
        <div className="px-2 py-1.5 font-mono text-[10px] text-muted-foreground">The same actions under ⌘K</div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
