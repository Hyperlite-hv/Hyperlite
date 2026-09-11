import { useEffect, useState } from "react";
import { Camera, RotateCcw, Trash2, GitBranch } from "lucide-react";
import ConfirmDialog from "../../components/ConfirmDialog";
import { useInfraStore } from "../../store/useInfraStore";

// Correspond a GET/POST/DELETE /vms/{name}/snapshots, deja fonctionnels cote
// backend reel -- meme forme de champs (nom, description, date_creation, actuel).
// `parent` est une extension purement front (le backend actuel ne modelise pas
// l'arbre de snapshots imbriques) pour illustrer le rendu visuel demande.
function seedSnapshots(vmName) {
  if (vmName === "db-primary") {
    return [
      { id: "s1", nom: "avant-migration-v2", description: "Avant migration schema v2", date: Date.now() - 5 * 86400000, actuel: false, parent: null },
      { id: "s2", nom: "post-migration-v2", description: "Apres migration, verifie OK", date: Date.now() - 4 * 86400000, actuel: false, parent: "s1" },
      { id: "s3", nom: "quotidien-auto", description: "Snapshot quotidien automatique", date: Date.now() - 3600000, actuel: true, parent: "s2" },
    ];
  }
  return [
    { id: "s1", nom: "initial", description: "Snapshot initial", date: Date.now() - 2 * 86400000, actuel: true, parent: null },
  ];
}

function SnapshotNode({ snap, depth, onRestore, onDelete }) {
  return (
    <div>
      <div className="flex items-center gap-2 rounded px-2 py-2 hover:bg-anthracite-700/50" style={{ paddingLeft: 8 + depth * 20 }}>
        {depth > 0 && <GitBranch size={13} className="text-anthracite-500 shrink-0" />}
        <Camera size={14} className="text-anthracite-400 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-anthracite-100">
            {snap.nom} {snap.actuel && <span className="ml-1 rounded bg-accent-blue/20 px-1.5 py-0.5 text-[10px] text-accent-blue">actuel</span>}
          </div>
          <div className="text-xs text-anthracite-400 truncate">{snap.description} -- {new Date(snap.date).toLocaleString("fr-FR")}</div>
        </div>
        <button className="btn-secondary" onClick={() => onRestore(snap)}><RotateCcw size={13} /> Restaurer</button>
        <button className="btn-danger" onClick={() => onDelete(snap)}><Trash2 size={13} /></button>
      </div>
    </div>
  );
}

export default function VMSnapshotsTab({ resource: vm }) {
  const [snapshots, setSnapshots] = useState(() => seedSnapshots(vm?.nom));
  const [pending, setPending] = useState(null); // { action: "restore"|"delete", snap }
  const pushToast = useInfraStore((s) => s.pushToast);

  useEffect(() => setSnapshots(seedSnapshots(vm?.nom)), [vm?.nom]);

  if (!vm) return null;

  function createSnapshot() {
    const id = `s${Date.now()}`;
    const current = snapshots.find((s) => s.actuel);
    setSnapshots((prev) => [
      ...prev.map((s) => ({ ...s, actuel: false })),
      { id, nom: `snap-${new Date().toLocaleDateString("fr-FR").replace(/\//g, "-")}`, description: "Cree manuellement", date: Date.now(), actuel: true, parent: current?.id || null },
    ]);
    pushToast({ kind: "success", title: "Snapshot cree", message: vm.nom });
  }

  function confirmAction() {
    if (!pending) return;
    const { action, snap } = pending;
    if (action === "delete") {
      setSnapshots((prev) => prev.filter((s) => s.id !== snap.id));
      pushToast({ kind: "success", title: "Snapshot supprime", message: snap.nom });
    } else {
      setSnapshots((prev) => prev.map((s) => ({ ...s, actuel: s.id === snap.id })));
      pushToast({ kind: "success", title: "Snapshot restaure", message: snap.nom });
    }
    setPending(null);
  }

  // Tri topologique simple (racines d'abord, puis enfants) pour un rendu en arbre indente.
  const byParent = (parentId, depth) => {
    const children = snapshots.filter((s) => s.parent === parentId);
    return children.flatMap((s) => [
      <SnapshotNode key={s.id} snap={s} depth={depth} onRestore={(sn) => setPending({ action: "restore", snap: sn })} onDelete={(sn) => setPending({ action: "delete", snap: sn })} />,
      ...byParent(s.id, depth + 1),
    ]);
  };

  return (
    <div className="space-y-3">
      <button className="btn-primary" onClick={createSnapshot}>
        <Camera size={14} /> Creer un snapshot
      </button>
      <div className="card divide-y divide-anthracite-600">
        {byParent(null, 0)}
      </div>

      <ConfirmDialog
        open={!!pending}
        title={pending?.action === "delete" ? `Supprimer '${pending.snap.nom}' ?` : `Restaurer '${pending?.snap.nom}' ?`}
        message={pending?.action === "delete" ? "Cette action est irreversible." : "L'etat actuel de la VM sera remplace par celui du snapshot."}
        confirmLabel={pending?.action === "delete" ? "Supprimer" : "Restaurer"}
        danger={pending?.action === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
