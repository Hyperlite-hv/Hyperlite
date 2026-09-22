import LoadingState from "../../components/LoadingState";
import { useEffect, useState } from "react";
import { Layers, Rocket, Trash2 } from "lucide-react";
import { fetchTemplates, deployTemplate, deleteTemplate } from "../../api/client";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";

// Real: GET/POST/DELETE /templates, already working on the backend (converting a
// VM to a template is done from the Summary tab of a stopped VM).
export default function TemplatesTab() {
  const isAdmin = useAuthStore(selectIsAdmin);
  const pushToast = useInfraStore((s) => s.pushToast);
  const loadAll = useInfraStore((s) => s.loadAll);
  const [templates, setTemplates] = useState(null);
  const [deployTarget, setDeployTarget] = useState(null);
  const [newName, setNewName] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [busy, setBusy] = useState(false);

  const reload = () => fetchTemplates().then(setTemplates).catch((e) => pushToast({ kind: "error", title: "Templates error", message: e.message }));
  useEffect(() => { reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (templates == null) return <Card className="p-4 text-sm text-muted-foreground"><LoadingState /></Card>;

  async function handleDeploy() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await deployTemplate(deployTarget.nom, newName.trim());
      pushToast({ kind: "success", title: "VM deployed", message: newName.trim() });
      setDeployTarget(null);
      setNewName("");
      await loadAll();
    } catch (e) {
      pushToast({ kind: "error", title: "Deployment failed", message: e.message });
    } finally { setBusy(false); }
  }

  async function handleDelete() {
    setBusy(true);
    try {
      await deleteTemplate(pendingDelete.nom);
      pushToast({ kind: "success", title: "Template deleted", message: pendingDelete.nom });
      setPendingDelete(null);
      reload();
    } catch (e) {
      pushToast({ kind: "error", title: "Deletion failed", message: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">Converting a (stopped) VM to a template is done from its Summary tab.</p>
      <Card className="p-0 divide-y divide-border">
        {templates.length === 0 && <div className="px-4 py-3 text-sm text-muted-foreground">No templates.</div>}
        {templates.map((t) => (
          <div key={t.nom} className="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors duration-150 hover:bg-muted/40">
            <Layers size={14} className="text-muted-foreground shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-foreground">{t.nom}</div>
              <div className="text-xs text-muted-foreground truncate">
                from {t.vm_source} -- {t.vcpu} vCPU / {t.memoire_mo} MB, created by {t.cree_par} on {t.cree_le}
              </div>
            </div>
            {isAdmin && (
              <>
                <Button aria-label={`Deploy template ${t.nom}`} variant="secondary" size="sm" disabled={busy} onClick={() => { setDeployTarget(t); setNewName(`${t.nom}-01`); }}>
                  <Rocket /> Deploy
                </Button>
                <Button aria-label={`Delete template ${t.nom}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" disabled={busy} onClick={() => setPendingDelete(t)}><Trash2 size={13} /></Button>
              </>
            )}
          </div>
        ))}
      </Card>

      <Dialog open={!!deployTarget} onOpenChange={(o) => { if (!o) setDeployTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Deploy "{deployTarget?.nom}"</DialogTitle>
          </DialogHeader>
          <div>
            <Label htmlFor="deploy-name" className="mb-1.5 text-xs font-medium text-muted-foreground">Name of the new VM</Label>
            <Input id="deploy-name" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeployTarget(null)}>Cancel</Button>
            <Button disabled={busy || !newName.trim()} onClick={handleDeploy}>Deploy</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!pendingDelete}
        title={`Delete the template '${pendingDelete?.nom}'?`}
        message="The template's disk will be permanently deleted."
        confirmLabel="Delete"
        onCancel={() => setPendingDelete(null)}
        onConfirm={handleDelete}
      />
    </div>
  );
}
