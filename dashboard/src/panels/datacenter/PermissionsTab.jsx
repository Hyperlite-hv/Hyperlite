// Le modele de roles ci-dessous EST reel (app/core/database.py : role IN ('admin',
// 'observateur')) -- Hyperlite n'a que ces deux roles, pas de systeme de
// permissions granulaires par ressource comme Proxmox. La liste d'utilisateurs
// en dessous est mock (pas de route GET /users cote backend aujourd'hui).
const roles = [
  { nom: "admin", description: "Acces complet : creation/suppression de VM, actions destructives, terminal SSH, console." },
  { nom: "observateur", description: "Lecture seule : consultation des VM, metriques, taches. Aucune action de modification." },
];

const mockUsers = [
  { nom: "admin", role: "admin" },
  { nom: "anthobo", role: "admin" },
];

export default function PermissionsTab() {
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-anthracite-100 mb-2">Roles (reels)</h3>
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
        <h3 className="text-sm font-semibold text-anthracite-100 mb-2">Utilisateurs (mock)</h3>
        <div className="divide-y divide-anthracite-600">
          {mockUsers.map((u) => (
            <div key={u.nom} className="flex justify-between py-2 text-sm">
              <span className="text-anthracite-100">{u.nom}</span>
              <span className="text-anthracite-400">{u.role}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
