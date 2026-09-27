import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Monitor } from "lucide-react";
import { fetchVM } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { errorMessage } from "../lib/errors";
import { StatePill, Loading } from "../components/ui";
import VmConsole from "../pages/VmConsole";
import StandaloneWindow from "./StandaloneWindow";

// /console/:name?mode=vnc|terminal: the console of a VM in a window of its own, the same one as the VM page,
// full height and connected by itself.
export default function ConsoleWindow() {
  const { name } = useParams();
  const [params] = useSearchParams();
  const initialMode = params.get("mode") === "terminal" ? "terminal" : "vnc";
  const status = useAuthStore((s) => s.status);
  const [vm, setVm] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (status !== "authenticated") return undefined;
    const load = () => fetchVM(name).then((v) => { setVm(v); setError(null); }).catch((e) => setError(errorMessage(e)));
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [name, status]);
  return (
    <StandaloneWindow title={name} icon={Monitor} badge={vm && <StatePill kind="vm" wire={vm.etat} />}>
      {error && <p className="nx-f-h is-error" role="alert">{error}</p>}
      {vm ? <VmConsole resource={vm} standalone initialMode={initialMode} /> : !error && <Loading />}
    </StandaloneWindow>
  );
}
