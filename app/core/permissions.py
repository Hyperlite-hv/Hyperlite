"""Permissions granulaires, a la Proxmox : Groupes d'utilisateurs + Pools de
VM + Roles scopes + Attributions (ACL), en plus des deux roles globaux
existants (admin/observateur, voir app/core/security.py -- inchanges).

Principe : additif uniquement. Un admin reste admin partout, un observateur
garde sa lecture globale sur tout ; les ACL ne font qu'ACCORDER des droits
supplementaires a un utilisateur ou groupe precis, sur une VM ou un pool
precis -- jamais retirer de droits existants. Pas de "deny" dans ce modele.
"""

from app.core.database import get_conn

# Roles scopes attribuables via une ACL (distincts des roles globaux
# admin/observateur). Volontairement fixes (pas de constructeur de role
# personnalise) pour rester simple : ces trois niveaux couvrent l'essentiel
# des besoins reels sans construire un editeur de privileges a la carte.
ROLES = {
    "lecteur": {
        "label": "Lecteur",
        "description": "Consultation (etat, metriques, journal) sur la ressource attribuee.",
        "privileges": {"vm.view"},
    },
    "operateur": {
        "label": "Operateur",
        "description": "Demarrer / arreter / redemarrer, console graphique et terminal SSH, sur la ressource attribuee.",
        "privileges": {"vm.view", "vm.power", "vm.console"},
    },
    "gestionnaire": {
        "label": "Gestionnaire",
        "description": "Operateur + snapshots, redimensionnement CPU/RAM/disque, materiel (disques/reseau) -- sans creation ni suppression de VM.",
        "privileges": {"vm.view", "vm.power", "vm.console", "vm.snapshot", "vm.resize", "vm.hardware"},
    },
}


# ---- Groupes ----

def list_groups():
    with get_conn() as conn:
        groups = [dict(r) for r in conn.execute("SELECT id, name FROM groups ORDER BY name").fetchall()]
        for g in groups:
            rows = conn.execute("SELECT username FROM group_members WHERE group_id = ? ORDER BY username", (g["id"],)).fetchall()
            g["membres"] = [r["username"] for r in rows]
        return groups


def create_group(name):
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO groups (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid


def delete_group(group_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM acl WHERE subject_type = 'group' AND subject_id = ?", (str(group_id),))
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        conn.commit()


def add_group_member(group_id, username):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)", (group_id, username))
        conn.commit()


def remove_group_member(group_id, username):
    with get_conn() as conn:
        conn.execute("DELETE FROM group_members WHERE group_id = ? AND username = ?", (group_id, username))
        conn.commit()


def get_user_groups(username):
    with get_conn() as conn:
        rows = conn.execute("SELECT group_id FROM group_members WHERE username = ?", (username,)).fetchall()
        return [r["group_id"] for r in rows]


# ---- Pools ----

def list_pools():
    with get_conn() as conn:
        pools = [dict(r) for r in conn.execute("SELECT id, name, description FROM pools ORDER BY name").fetchall()]
        for p in pools:
            rows = conn.execute("SELECT vm_name FROM pool_members WHERE pool_id = ? ORDER BY vm_name", (p["id"],)).fetchall()
            p["vms"] = [r["vm_name"] for r in rows]
        return pools


def create_pool(name, description=""):
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO pools (name, description) VALUES (?, ?)", (name, description))
        conn.commit()
        return cur.lastrowid


def delete_pool(pool_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM pool_members WHERE pool_id = ?", (pool_id,))
        conn.execute("DELETE FROM acl WHERE resource_type = 'pool' AND resource_id = ?", (str(pool_id),))
        conn.execute("DELETE FROM pools WHERE id = ?", (pool_id,))
        conn.commit()


def add_pool_member(pool_id, vm_name):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO pool_members (pool_id, vm_name) VALUES (?, ?)", (pool_id, vm_name))
        conn.commit()


def remove_pool_member(pool_id, vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM pool_members WHERE pool_id = ? AND vm_name = ?", (pool_id, vm_name))
        conn.commit()


def rename_pool_member(old_vm_name, new_vm_name):
    """VM clonee/renommee : conserve son appartenance aux pools sous le nouveau nom."""
    with get_conn() as conn:
        conn.execute("UPDATE pool_members SET vm_name = ? WHERE vm_name = ?", (new_vm_name, old_vm_name))
        conn.execute("DELETE FROM acl WHERE resource_type = 'vm' AND resource_id = ?", (old_vm_name,))
        conn.commit()


def get_vm_pools(vm_name):
    with get_conn() as conn:
        rows = conn.execute("SELECT pool_id FROM pool_members WHERE vm_name = ?", (vm_name,)).fetchall()
        return [r["pool_id"] for r in rows]


def remove_vm_from_all_pools(vm_name):
    """VM supprimee : retire son appartenance aux pools (evite des entrees
    orphelines qui pointeraient vers une VM qui n'existe plus)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM pool_members WHERE vm_name = ?", (vm_name,))
        conn.commit()


# ---- ACL ----

def list_acl():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, subject_type, subject_id, role, resource_type, resource_id FROM acl ORDER BY id").fetchall()
        acl = [dict(r) for r in rows]
        groups = {g["id"]: g["name"] for g in list_groups()}
        pools = {p["id"]: p["name"] for p in list_pools()}
        for a in acl:
            if a["subject_type"] == "group":
                a["subject_label"] = groups.get(int(a["subject_id"]), f"groupe#{a['subject_id']}")
            else:
                a["subject_label"] = a["subject_id"]
            if a["resource_type"] == "pool":
                a["resource_label"] = pools.get(int(a["resource_id"]), f"pool#{a['resource_id']}")
            else:
                a["resource_label"] = a["resource_id"]
        return acl


def create_acl(subject_type, subject_id, role, resource_type, resource_id):
    if role not in ROLES:
        raise ValueError(f"Role inconnu : {role}")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO acl (subject_type, subject_id, role, resource_type, resource_id) VALUES (?, ?, ?, ?, ?)",
            (subject_type, subject_id, role, resource_type, resource_id),
        )
        conn.commit()
        return cur.lastrowid


def delete_acl(acl_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM acl WHERE id = ?", (acl_id,))
        conn.commit()


def delete_acl_for_vm(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM acl WHERE resource_type = 'vm' AND resource_id = ?", (vm_name,))
        conn.commit()


# ---- Verification effective ----

def has_privilege(user, vm_name, privilege):
    """True si `user` a le privilege demande sur `vm_name`, via son role
    global (admin/observateur) OU une ACL (directe ou via un groupe) sur
    cette VM OU un pool qui la contient."""
    if user["role"] == "admin":
        return True
    if user["role"] == "observateur" and privilege in {"vm.view", "vm.console"}:
        return True  # comportement historique inchange : lecture + console VNC en lecture globale (pas le terminal SSH, deja admin-only avant ce systeme)

    group_ids = set(get_user_groups(user["username"]))
    pool_ids = set(get_vm_pools(vm_name))

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, resource_type, resource_id, subject_type, subject_id FROM acl"
        ).fetchall()

    for row in rows:
        subject_ok = (
            (row["subject_type"] == "user" and row["subject_id"] == user["username"])
            or (row["subject_type"] == "group" and row["subject_id"].isdigit() and int(row["subject_id"]) in group_ids)
        )
        if not subject_ok:
            continue
        resource_ok = (
            (row["resource_type"] == "vm" and row["resource_id"] == vm_name)
            or (row["resource_type"] == "pool" and row["resource_id"].isdigit() and int(row["resource_id"]) in pool_ids)
        )
        if not resource_ok:
            continue
        if privilege in ROLES.get(row["role"], {}).get("privileges", set()):
            return True
    return False
