"""Granular permissions, Proxmox-style: user groups + VM pools + scoped roles +
assignments (ACLs), on top of the two existing global roles (admin/observateur,
see app/core/security.py, unchanged).

Principle: additive only. An admin remains admin everywhere and an observer
keeps global read access to everything; ACLs only GRANT additional rights to a
specific user or group on a specific VM or pool, and never remove existing
rights. There is no "deny" in this model.

"""

from app.core.database import get_conn

# Full catalog of the privileges available to build a custom role (see
# custom_roles below). Adding a privilege here exposes it in the role builder of
# the dashboard; enforcing it still has to be wired endpoint by endpoint through
# require_vm_privilege (see app/routers/vms.py).
ALL_PRIVILEGES = {
    "vm.view": "View (state, metrics, journal)",
    "vm.power": "Start / stop / restart",
    "vm.console": "Graphical console (VNC) and SSH terminal",
    "vm.snapshot": "Snapshots (create / restore / delete)",
    "vm.resize": "Resize (CPU / RAM / disk)",
    "vm.hardware": "Hardware (disks, network interfaces, CD drive)",
    "vm.clone": "Clone the VM (creates a new VM and consumes disk space)",
    # LXC containers: a catalog distinct from vm.* (a different resource, even
    # though `has_container_privilege` reuses the SAME predefined roles below).
    "container.view": "View (state, IP)",
    "container.power": "Start / stop",
    "container.console": "Terminal SSH web",
}

# Predefined, scoped roles that can be assigned through an ACL (distinct from
# the global admin/observateur roles): convenient shortcuts over the same
# privilege catalog as the custom roles below, not a separate mechanism.
ROLES = {
    "lecteur": {
        "label": "Reader",
        "description": "View (state, metrics, journal) on the assigned resource.",
        "privileges": {"vm.view", "container.view"},
    },
    "operateur": {
        "label": "Operator",
        "description": "Start / stop / restart, graphical console and SSH terminal, on the assigned resource.",
        "privileges": {"vm.view", "vm.power", "vm.console", "container.view", "container.power", "container.console"},
    },
    "gestionnaire": {
        "label": "Manager",
        "description": "Operator + snapshots, CPU/RAM/disk resizing, hardware (disks/network), without creating or deleting VMs.",
        "privileges": {
            "vm.view",
            "vm.power",
            "vm.console",
            "vm.snapshot",
            "vm.resize",
            "vm.hardware",
            "container.view",
            "container.power",
            "container.console",
        },
    },
}


# ---- Custom roles ----
# Same principle as the predefined roles above, but the privilege set is chosen
# freely (see ALL_PRIVILEGES). They are identified in acl.role by the string
# "custom:<id>" so they can never collide with the keys of the predefined roles.


def _custom_role_key(row):
    return f"custom:{row['id']}"


def list_custom_roles():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, privileges FROM custom_roles ORDER BY name").fetchall()
    return [
        {
            "key": _custom_role_key(r),
            "id": r["id"],
            "label": r["name"],
            "description": "Custom role.",
            "privileges": set(r["privileges"].split(",")) if r["privileges"] else set(),
        }
        for r in rows
    ]


def create_custom_role(name, privileges):
    invalid = set(privileges) - set(ALL_PRIVILEGES)
    if invalid:
        raise ValueError(f"Unknown privileges: {', '.join(sorted(invalid))}")
    if not privileges:
        raise ValueError("Choose at least one privilege")
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO custom_roles (name, privileges) VALUES (?, ?)", (name, ",".join(privileges)))
        conn.commit()
        return cur.lastrowid


def delete_custom_role(role_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM acl WHERE role = ?", (f"custom:{role_id}",))
        conn.execute("DELETE FROM custom_roles WHERE id = ?", (role_id,))
        conn.commit()


def get_role_privileges(role_key):
    """Resolve a predefined OR custom role to its privilege set. Returns an empty
    set if the role no longer exists (e.g. deleted in the meantime): the ACL
    that referenced it simply becomes harmless instead of crashing the check."""
    if role_key in ROLES:
        return ROLES[role_key]["privileges"]
    if role_key.startswith("custom:"):
        try:
            role_id = int(role_key.split(":", 1)[1])
        except ValueError:
            return set()
        with get_conn() as conn:
            row = conn.execute("SELECT privileges FROM custom_roles WHERE id = ?", (role_id,)).fetchone()
        return set(row["privileges"].split(",")) if row and row["privileges"] else set()
    return set()


def role_exists(role_key):
    if role_key in ROLES:
        return True
    if not role_key.startswith("custom:"):
        return False
    tail = role_key.split(":", 1)[1]
    if not tail.isdigit():
        return False
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM custom_roles WHERE id = ?", (int(tail),)).fetchone() is not None


# ---- Groups ----


def list_groups():
    with get_conn() as conn:
        groups = [dict(r) for r in conn.execute("SELECT id, name FROM groups ORDER BY name").fetchall()]
        for g in groups:
            rows = conn.execute(
                "SELECT username FROM group_members WHERE group_id = ? ORDER BY username", (g["id"],)
            ).fetchall()
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
            rows = conn.execute(
                "SELECT vm_name FROM pool_members WHERE pool_id = ? ORDER BY vm_name", (p["id"],)
            ).fetchall()
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
    """VM cloned/renamed: keep its pool membership under the new name."""
    with get_conn() as conn:
        conn.execute("UPDATE pool_members SET vm_name = ? WHERE vm_name = ?", (new_vm_name, old_vm_name))
        conn.execute("DELETE FROM acl WHERE resource_type = 'vm' AND resource_id = ?", (old_vm_name,))
        conn.commit()


def get_vm_pools(vm_name):
    with get_conn() as conn:
        rows = conn.execute("SELECT pool_id FROM pool_members WHERE vm_name = ?", (vm_name,)).fetchall()
        return [r["pool_id"] for r in rows]


def remove_vm_from_all_pools(vm_name):
    """VM deleted: remove its pool membership (avoids orphaned entries pointing to a
    VM that no longer exists)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM pool_members WHERE vm_name = ?", (vm_name,))
        conn.commit()


# ---- ACL ----


def list_acl():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, subject_type, subject_id, role, resource_type, resource_id FROM acl ORDER BY id"
        ).fetchall()
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
    if not role_exists(role):
        raise ValueError(f"Unknown role: {role}")
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


# ---- Effective permission check ----


def has_privilege(user, vm_name, privilege):
    """True if `user` has the requested privilege on `vm_name`, through their global
    role (admin/observateur) OR an ACL (direct or through a group) on this VM
    OR on a pool that contains it."""
    if user["role"] == "admin":
        return True
    if user["role"] == "observateur" and privilege in {"vm.view", "vm.console"}:
        return True  # unchanged historical behaviour: read + VNC console at the global read level (not the SSH terminal, which was already admin-only before this system)

    group_ids = set(get_user_groups(user["username"]))
    pool_ids = set(get_vm_pools(vm_name))

    with get_conn() as conn:
        rows = conn.execute("SELECT role, resource_type, resource_id, subject_type, subject_id FROM acl").fetchall()

    for row in rows:
        subject_ok = (row["subject_type"] == "user" and row["subject_id"] == user["username"]) or (
            row["subject_type"] == "group" and row["subject_id"].isdigit() and int(row["subject_id"]) in group_ids
        )
        if not subject_ok:
            continue
        resource_ok = (row["resource_type"] == "vm" and row["resource_id"] == vm_name) or (
            row["resource_type"] == "pool" and row["resource_id"].isdigit() and int(row["resource_id"]) in pool_ids
        )
        if not resource_ok:
            continue
        if privilege in get_role_privileges(row["role"]):
            return True
    return False


def has_container_privilege(user, container_name, privilege):
    """Equivalent of has_privilege() for LXC containers: the same role catalog
    (lecteur/operateur/gestionnaire/custom), with resource_type='container' in
    the ACL instead of 'vm'. There is NO grouping by pool for containers in
    this first pass (the pools of app/routers/pools.py are a VM-only concept,
    "VM added to the pool"): a known limitation, documented rather than
    silently absent."""
    if user["role"] == "admin":
        return True
    if user["role"] == "observateur" and privilege == "container.view":
        return True  # same principle as vm.view: global read access for observers, not the terminal

    group_ids = set(get_user_groups(user["username"]))

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, resource_type, resource_id, subject_type, subject_id FROM acl WHERE resource_type = 'container'"
        ).fetchall()

    for row in rows:
        subject_ok = (row["subject_type"] == "user" and row["subject_id"] == user["username"]) or (
            row["subject_type"] == "group" and row["subject_id"].isdigit() and int(row["subject_id"]) in group_ids
        )
        if not subject_ok or row["resource_id"] != container_name:
            continue
        if privilege in get_role_privileges(row["role"]):
            return True
    return False
