import { useCallback, useEffect, useState } from "react";
import { PackageOpen, Download, Trash2 } from "lucide-react";
import { fetchVmExports, downloadVmExport, deleteVmExport } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import ConfirmDialog from "../../components/ConfirmDialog";

function formatSize(bytes) {
  if (!bytes) return "--";
  const go = bytes / (1024 ** 3);
  return go >= 1 ? `${go.toFixed(2)} GB` : `${(bytes / (1024 ** 2)).toFixed(0)} MB`;
}

// Real: GET /vm-exports: qcow2 files produced by "Export disk" on a VM (see
// VMActionMenu.jsx), ready to download. The same periodic refresh mechanism as
// BackupsTab/ContainersTab while an export may be running (no live progress
// tracking here, see the note in app/routers/vm_export.py; the same limit is
// already accepted for backups).
export default function ExportsTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [toDelete, setToDelete] = useState(null);

  const reload = useCallback(() => {
    fetchVmExports().then(setRows).catch((e) => pushToast({ kind: "error", title: "Exports error", message: e.message }));
  }, [pushToast]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    const id = setInterval(reload, 8000);
    return () => clearInterval(id);
  }, [reload]);

  async function handleDownload(nom) {
    try {
      await downloadVmExport(nom);
    } catch (e) {
      pushToast({ kind: "error", title: "Download failed", message: e.message });
    }
  }

  async function handleDelete() {
    if (!toDelete) return;
    try {
      await deleteVmExport(toDelete.nom);
      pushToast({ kind: "success", title: "Export deleted", message: toDelete.nom });
      setToDelete(null);
      await reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  if (rows == null) return <div className="card p-4 text-sm text-anthracite-400">Loading...</div>;

  if (rows.length === 0) {
    return (
      <div className="card flex flex-col items-center gap-2 p-8 text-center">
        <PackageOpen size={26} className="text-anthracite-400" />
        <p className="text-sm text-anthracite-300">No exports yet.</p>
        <p className="text-xs text-anthracite-500 max-w-sm">
          From a VM, open its actions menu and choose "Export disk" to produce a file that can be downloaded here (system disk only).
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="card divide-y divide-anthracite-600">
        <div className="grid grid-cols-[1fr_110px_140px_90px] gap-2 px-4 py-2 text-xs font-medium text-anthracite-400">
          <span>File</span><span>Size</span><span>Created on</span><span></span>
        </div>
        {rows.map((r) => (
          <div key={r.nom} className="grid grid-cols-[1fr_110px_140px_90px] gap-2 px-4 py-2 text-sm items-center">
            <span className="text-anthracite-100 truncate font-mono text-xs" title={r.nom}>{r.nom}</span>
            <span className="text-xs text-anthracite-400">{formatSize(r.taille_octets)}</span>
            <span className="text-anthracite-400 text-xs font-mono">{new Date(r.modifie_le).toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
            <div className="flex justify-end gap-1.5">
              <button aria-label="Download" className="btn-secondary" title="Download" onClick={() => handleDownload(r.nom)}><Download size={13} /></button>
              <button aria-label="Delete" className="btn-danger" title="Delete" onClick={() => setToDelete(r)}><Trash2 size={13} /></button>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={!!toDelete}
        title="Delete the export"
        message={`Permanently delete the file "${toDelete?.nom}"?`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
