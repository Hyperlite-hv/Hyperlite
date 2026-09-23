import { useEffect, useState } from "react";
import { HardDrive } from "lucide-react";
import { fetchIsoTemplates, fetchVmDisks } from "../../api/client";
import { detectOsFamily } from "../../utils/osFamily";
import VmDiskUploadDropzone from "../../components/VmDiskUploadDropzone";
import IsoUploadDropzone from "../../components/IsoUploadDropzone";

// Three distinct cases on the real backend side (app/core/vm_builder.py +
// app/core/unattended_install.py + POST /vms):
// - "None" ISO -> a preinstalled system disk (Debian 12 cloud-init), direct access
//   (user/password defined here, working web SSH terminal).
// - A recognized ISO (RHEL/CentOS/Rocky/Alma/Fedora -> kickstart, Ubuntu ->
//   autoinstall) -> a BLANK disk but an unattended installation: the user account
//   (defined in the next step) and the automation SSH key are installed
//   automatically, with a working web SSH terminal as soon as the installation
//   ends, like with cloud-init.
// - An unrecognized ISO -> a BLANK disk, manual installation through the VNC
//   console, the account is created by the installer (no web SSH terminal until
//   access is configured by hand).
export default function StepTemplate({ form, patch }) {
  const [isos, setIsos] = useState([]);
  const [disks, setDisks] = useState([]);
  useEffect(() => { fetchIsoTemplates().then(setIsos); }, []);
  const reloadDisks = () => fetchVmDisks().then(setDisks);
  useEffect(() => { reloadDisks(); }, []);
  const osFamily = detectOsFamily(form.iso);
  const importMode = form.importDisk != null;
  useEffect(() => {
    if (importMode && !form.importDisk && disks.length > 0) patch({ importDisk: disks[0].nom });
  }, [importMode, form.importDisk, disks, patch]);

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          className={!importMode ? "btn-primary flex-1 py-1.5! text-xs" : "btn-secondary flex-1 py-1.5! text-xs"}
          onClick={() => patch({ importDisk: null })}
        >
          Base image / ISO
        </button>
        <button
          type="button"
          className={importMode ? "btn-primary flex-1 py-1.5! text-xs" : "btn-secondary flex-1 py-1.5! text-xs"}
          onClick={() => patch({ iso: "", importDisk: disks[0]?.nom || "__pending__" })}
        >
          Import an existing disk
        </button>
      </div>

      {importMode ? (
        <div className="space-y-3">
          <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-200">
            System disk: <span className="text-anthracite-100 font-medium">imported, as is</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              The VM boots straight from this disk (an OS and accounts are already on it): no account to define here, and no automatic web SSH terminal until the Hyperlite key is already present on it.
            </div>
          </div>

          {disks.length > 0 && (
            <div className="space-y-1.5">
              {disks.map((d) => (
                <label key={d.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${form.importDisk === d.nom ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"}`}>
                  <input type="radio" checked={form.importDisk === d.nom} onChange={() => patch({ importDisk: d.nom })} className="accent-accent-blue" />
                  <HardDrive size={14} className="text-anthracite-400 shrink-0" />
                  <div>
                    <div className="text-sm text-anthracite-100">{d.nom}</div>
                    <div className="text-xs text-anthracite-400">{d.taille_mo} MB</div>
                  </div>
                </label>
              ))}
            </div>
          )}

          <VmDiskUploadDropzone onDone={() => reloadDisks().then(() => {})} />
        </div>
      ) : (
      <>
      <div className="rounded-md border border-anthracite-600 px-3 py-2.5 text-sm text-anthracite-200">
        {!form.iso ? (
          <>
            Base image: <span className="text-anthracite-100 font-medium">Debian 12 (cloud-init)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">Preinstalled and ready to use (user/password defined in the next step).</div>
          </>
        ) : osFamily === "kickstart" ? (
          <>
            System disk: <span className="text-anthracite-100 font-medium">blank, unattended installation (Kickstart)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              The account defined in the next step and the Hyperlite SSH key are installed automatically: the web SSH terminal works once the installation is finished, with no intervention.
            </div>
          </>
        ) : osFamily === "autoinstall" ? (
          <>
            System disk: <span className="text-anthracite-100 font-medium">blank, unattended installation (autoinstall)</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              The account defined in the next step and the Hyperlite SSH key are installed automatically. Ubuntu asks for a single confirmation ("Continue with autoinstall?"): press Enter once in the VNC console at boot, and the rest is automatic.
            </div>
          </>
        ) : (
          <>
            System disk: <span className="text-anthracite-100 font-medium">blank, to be installed manually</span>
            <div className="text-xs text-anthracite-400 mt-0.5">
              ISO not recognized for unattended installation: the VM will boot from it for a manual installation through the VNC console. The user account will be created during the installation (no automatic web SSH terminal for this VM until you have configured access yourself).
            </div>
          </>
        )}
      </div>
      <p className="text-sm text-anthracite-300">Installation ISO (optional):</p>
      <label className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${!form.iso ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600"}`}>
        <input type="radio" checked={!form.iso} onChange={() => patch({ iso: "" })} className="accent-accent-blue" />
        <span className="text-sm text-anthracite-100">None</span>
      </label>
      {isos.map((iso) => (
        <label key={iso.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer ${form.iso === iso.nom ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"}`}>
          <input type="radio" checked={form.iso === iso.nom} onChange={() => patch({ iso: iso.nom })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-anthracite-100">{iso.nom}</div>
            <div className="text-xs text-anthracite-400">{iso.taille_mo} MB</div>
          </div>
        </label>
      ))}
      {form.iso && (
        <div className="space-y-2 rounded-md border border-anthracite-600 p-3">
          <label className="block text-sm text-anthracite-100" htmlFor="drivers-iso">Windows / additional drivers ISO (optional)</label>
          <select id="drivers-iso" className="input" value={form.driversIso || ""} onChange={(e) => patch({ driversIso: e.target.value })}>
            <option value="">None</option>
            {isos.filter((iso) => iso.nom !== form.iso).map((iso) => <option key={iso.nom} value={iso.nom}>{iso.nom}</option>)}
          </select>
          <p className="text-xs text-anthracite-300">
            Installing Windows? Upload and select the VirtIO Windows driver ISO. It stays in a separate CD drive alongside the Windows installer.
            At the disk selection screen, choose Load driver and browse to the vioscsi folder for your Windows version and amd64 architecture.
            After installation, run the VirtIO guest tools from this CD for network and other drivers.
          </p>
          <a className="text-xs text-accent-blue underline" href="https://virtio-win.github.io/Knowledge-Base/Driver-installation.html" target="_blank" rel="noreferrer">Get VirtIO Windows drivers and installation instructions</a>
          <IsoUploadDropzone onDone={() => fetchIsoTemplates().then(setIsos)} />
        </div>
      )}
      </>
      )}
    </div>
  );
}
