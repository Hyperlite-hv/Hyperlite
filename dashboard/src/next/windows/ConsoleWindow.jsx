import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Monitor } from "lucide-react";
import { fetchVM } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { errorMessage } from "../lib/errors";
import { StatePill, Loading } from "../components/ui";
import VmConsole from "../pages/VmConsole";
import StandaloneWindow from "./StandaloneWindow";

// /console/:name?node=<node>&mode=vnc|terminal: the console of a VM in a window of its own, the same one as the VM
// page, full height and connected by itself. `node` is absent for a VM of the local host.
export default function ConsoleWindow() {
  const { name } = useParams();
  const [params] = useSearchParams();
  const initialMode = params.get("mode") === "terminal" ? "terminal" : "vnc";
  const node = params.get("node") || null;
  const status = useAuthStore((s) => s.status);
  const [vm, setVm] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (status !== "authenticated") return undefined;
    // The state endpoint answers only the VM's details, it does not carry the node: it is set from the URL.
    const load = () => fetchVM(name, node).then((v) => { setVm({ ...v, node: node || "local" }); setError(null); }).catch((e) => setError(errorMessage(e)));
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [name, node, status]);
  return (
    <StandaloneWindow title={name} icon={Monitor} badge={vm && <StatePill kind="vm" wire={vm.etat} />}>
      {error && <p className="nx-f-h is-error" role="alert">{error}</p>}
      {vm ? <VmConsole resource={vm} standalone initialMode={initialMode} /> : !error && <Loading />}
    </StandaloneWindow>
  );
}
