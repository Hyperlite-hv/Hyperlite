import { useEffect, useState } from "react";
import { ejectVMDriversIso, fetchIsoTemplates, mountVMDriversIso } from "../api/client";
import { useInfraStore } from "../store/useInfraStore";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";

export default function DriversIsoControl({ vmName, onChanged }) {
  const [isos, setIsos] = useState([]);
  const [iso, setIso] = useState("");
  const [busy, setBusy] = useState(false);
  const pushToast = useInfraStore((s) => s.pushToast);

  useEffect(() => {
    fetchIsoTemplates().then(setIsos).catch((e) => pushToast({ kind: "error", title: "ISO list failed", message: e.message }));
  }, [pushToast]);

  async function changeMedia(eject) {
    setBusy(true);
    try {
      if (eject) await ejectVMDriversIso(vmName);
      else await mountVMDriversIso(vmName, iso);
      await onChanged();
      pushToast({ kind: "success", title: eject ? "Driver ISO ejected" : "Driver ISO mounted" });
    } catch (e) {
      pushToast({ kind: "error", title: "Driver media change failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-2 border-t border-anthracite-600 px-4 py-3">
      <label htmlFor="vm-driver-iso" className="block text-sm text-anthracite-100">Windows drivers CD (hdd)</label>
      <p className="text-xs text-anthracite-400">Upload a VirtIO driver ISO in Storage, then mount it here. Shut down the VM first if this CD drive does not exist yet. The installation CD is kept in place.</p>
      <NativeSelect id="vm-driver-iso" className="w-full" value={iso} onChange={(e) => setIso(e.target.value)}>
        <option value="">Select a driver ISO</option>
        {isos.map((item) => <option key={item.nom} value={item.nom}>{item.nom}</option>)}
      </NativeSelect>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={busy || !iso} onClick={() => changeMedia(false)}>Mount drivers</Button>
        <Button variant="secondary" disabled={busy} onClick={() => changeMedia(true)}>Eject drivers</Button>
      </div>
      <p className="text-xs text-anthracite-400">In Windows Setup, choose Load driver, then the vioscsi driver for your Windows version (amd64). After installation, run the VirtIO guest tools for network and other drivers.</p>
    </div>
  );
}
