import { useEffect, useState } from "react";
import { fetchUsers } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";

// Le modele de roles ci-dessous est reel (app/core/database.py : role IN
// ('admin', 'observateur')) -- Hyperlite n'a que ces deux roles, pas de
// systeme de permissions granulaires par ressource comme Proxmox. La liste
// d'utilisateurs vient de GET /auth/users (reel).
const roles = [
  { nom: "admin", description: "Acces complet : creation/suppression de VM, actions destructives, terminal SSH, console." },
  { nom: "observateur", description: "Lecture seule : consultation des VM, metriques, taches. Aucune action de modification." },
];

export default function PermissionsTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [users, setUsers] = useState(null);

  useEffect(() => {
    fetchUsers().then(setUsers).catch((e) => pushToast({ kind: "error", title: "Erreur utilisateurs", message: e.message }));
  }, [pushToast]);

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-anthracite-100 mb-2">Roles</h3>
        <div className="divide-y divide-anthracite-600">
          {roles.map((r) => (
            <div key={r.nom} className="py-2">
              <div className="text-sm font-medium text-anthracite-100">{r.nom}</div>
              <div className="text-xs text-anthracite-400">{r.description}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-anthracite-100 mb-2">Utilisateurs</h3>
        {users == null ? (
          <p className="text-sm text-anthracite-400">Chargement...</p>
        ) : (
          <div className="divide-y divide-anthracite-600">
            {users.map((u) => (
              <div key={u.username} className="flex justify-between py-2 text-sm">
                <span className="text-anthracite-100">{u.username}</span>
                <span className="text-anthracite-400">{u.role}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
