import { useEffect, useState } from "react";
import { HardDrive } from "lucide-react";
import { fetchIsoTemplates, fetchVmDisks } from "../../api/client";
import { detectOsFamily } from "../../utils/osFamily";
import VmDiskUploadDropzone from "../../components/VmDiskUploadDropzone";
import { Button } from "@/components/ui/button";

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
        <Button
          type="button"
          variant={!importMode ? "default" : "secondary"}
          size="sm"
          className="flex-1"
          onClick={() => patch({ importDisk: "" })}
        >
          Base image / ISO
        </Button>
        <Button
          type="button"
          variant={importMode ? "default" : "secondary"}
          size="sm"
          className="flex-1"
          onClick={() => patch({ iso: "", importDisk: disks[0]?.nom || "__pending__" })}
        >
          Import an existing disk
        </Button>
      </div>

      {importMode ? (
        <div className="space-y-3">
          <div className="rounded-md border border-border px-3 py-2.5 text-sm text-foreground/90">
            System disk: <span className="text-foreground font-medium">imported, as is</span>
            <div className="text-xs text-muted-foreground mt-0.5">
              The VM boots straight from this disk (an OS and accounts are already on it): no account to define here, and no automatic web SSH terminal until the Hyperlite key is already present on it.
            </div>
          </div>

          {disks.length > 0 && (
            <div className="space-y-1.5">
              {disks.map((d) => (
                <label key={d.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer transition-colors duration-150 ${form.importDisk === d.nom ? "border-accent-blue bg-accent-blue/10" : "border-border hover:border-muted-foreground/40"}`}>
                  <input type="radio" checked={form.importDisk === d.nom} onChange={() => patch({ importDisk: d.nom })} className="accent-accent-blue" />
                  <HardDrive size={14} className="text-muted-foreground shrink-0" />
                  <div>
                    <div className="text-sm text-foreground">{d.nom}</div>
                    <div className="text-xs text-muted-foreground">{d.taille_mo} MB</div>
                  </div>
                </label>
              ))}
            </div>
          )}

          <VmDiskUploadDropzone onDone={() => reloadDisks().then(() => {})} />
        </div>
      ) : (
      <>
      <div className="rounded-md border border-border px-3 py-2.5 text-sm text-foreground/90">
        {!form.iso ? (
          <>
            Base image: <span className="text-foreground font-medium">Debian 12 (cloud-init)</span>
            <div className="text-xs text-muted-foreground mt-0.5">Preinstalled and ready to use (user/password defined in the next step).</div>
          </>
        ) : osFamily === "kickstart" ? (
          <>
            System disk: <span className="text-foreground font-medium">blank, unattended installation (Kickstart)</span>
            <div className="text-xs text-muted-foreground mt-0.5">
              The account defined in the next step and the Hyperlite SSH key are installed automatically: the web SSH terminal works once the installation is finished, with no intervention.
            </div>
          </>
        ) : osFamily === "autoinstall" ? (
          <>
            System disk: <span className="text-foreground font-medium">blank, unattended installation (autoinstall)</span>
            <div className="text-xs text-muted-foreground mt-0.5">
              The account defined in the next step and the Hyperlite SSH key are installed automatically. Ubuntu asks for a single confirmation ("Continue with autoinstall?"): press Enter once in the VNC console at boot, and the rest is automatic.
            </div>
          </>
        ) : (
          <>
            System disk: <span className="text-foreground font-medium">blank, to be installed manually</span>
            <div className="text-xs text-muted-foreground mt-0.5">
              ISO not recognized for unattended installation: the VM will boot from it for a manual installation through the VNC console. The user account will be created during the installation (no automatic web SSH terminal for this VM until you have configured access yourself).
            </div>
          </>
        )}
      </div>
      <p className="text-sm text-foreground/80">Installation ISO (optional):</p>
      <label className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer transition-colors duration-150 ${!form.iso ? "border-accent-blue bg-accent-blue/10" : "border-border"}`}>
        <input type="radio" checked={!form.iso} onChange={() => patch({ iso: "" })} className="accent-accent-blue" />
        <span className="text-sm text-foreground">None</span>
      </label>
      {isos.map((iso) => (
        <label key={iso.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer transition-colors duration-150 ${form.iso === iso.nom ? "border-accent-blue bg-accent-blue/10" : "border-border hover:border-muted-foreground/40"}`}>
          <input type="radio" checked={form.iso === iso.nom} onChange={() => patch({ iso: iso.nom })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-foreground">{iso.nom}</div>
            <div className="text-xs text-muted-foreground">{iso.taille_mo} MB</div>
          </div>
        </label>
      ))}
      </>
      )}
    </div>
  );
}
