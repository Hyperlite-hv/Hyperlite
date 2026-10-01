"""Granular permissions, Proxmox-style: user groups + VM pools + scoped roles +
assignments (ACLs), on top of the two existing global roles (admin/observateur,
see app/core/security.py, unchanged).

Principle: additive only. An admin remains admin everywhere and an observer
keeps global read access to everything; ACLs only GRANT additional rights to a
specific user or group on a specific VM or pool, and never remove existing
rights. There is no "deny" in this model.

"""


def _store():
    from app.repositories import registry

    return registry.access().sync


# Full catalog of the privileges available to build a custom role (see
# custom_roles below). Adding a privilege here exposes it in the role builder of
# the dashboard; enforcing it still has to be wired endpoint by endpoint through
# require_vm_privilege (see app/routers/vms.py).
ALL_PRIVILEGES = {
    "vm.view": "View (state, metrics, journal)",
    "vm.power": "Start / stop / restart",
    "vm.console": "Graphical console (VNC) and SSH terminal",
    # Network reachability of the VM from the user's own workstation (the `hyperlite`
    # client relays SSH or remote desktop): distinct from vm.console because it opens
    # the guest's own services to the workstation, not a console inside the browser.
    "vm.tunnel": "Tunnel from your workstation (SSH, remote desktop)",
    "vm.snapshot": "Snapshots (create / restore / delete)",
    "vm.resize": "Resize (CPU / RAM / disk)",
    "vm.hardware": "Hardware (disks, network interfaces, CD drive)",
    "vm.clone": "Clone the VM (creates a new VM and consumes disk space)",
    "vm.options": "Options (start at boot, notes and tags)",
    # LXC containers: a catalog distinct from vm.* (a different resource, even
    # though `has_container_privilege` reuses the SAME predefined roles below).
    "container.view": "View (state, IP)",
    "container.power": "Start / stop",
    "container.console": "Terminal SSH web",
    "container.options": "Options (notes and tags)",
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
        "description": "Start / stop / restart, graphical console, SSH terminal and workstation tunnel, on the assigned resource.",
        "privileges": {
            "vm.view",
            "vm.power",
            "vm.console",
            "vm.tunnel",
            "container.view",
            "container.power",
            "container.console",
        },
    },
    "gestionnaire": {
        "label": "Manager",
        "description": "Operator + snapshots, CPU/RAM/disk resizing, hardware (disks/network), options (start at boot, notes, tags), without creating or deleting VMs.",
        "privileges": {
            "vm.view",
            "vm.power",
            "vm.console",
            "vm.tunnel",
            "vm.snapshot",
            "vm.resize",
            "vm.hardware",
            "vm.options",
            "container.view",
            "container.power",
            "container.console",
            "container.options",
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
    return [
        {
            "key": _custom_role_key(r),
            "id": r["id"],
            "label": r["name"],
            "description": "Custom role.",
            "privileges": set(r["privileges"].split(",")) if r["privileges"] else set(),
        }
        for r in _store().custom_roles()
    ]


def create_custom_role(name, privileges):
    invalid = set(privileges) - set(ALL_PRIVILEGES)
    if invalid:
        raise ValueError(f"Unknown privileges: {', '.join(sorted(invalid))}")
    if not privileges:
        raise ValueError("Choose at least one privilege")
    return _store().create_custom_role(name, ",".join(privileges))


def delete_custom_role(role_id):
    _store().delete_custom_role(f"custom:{role_id}", role_id)


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
        row = _store().custom_role(role_id)
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
    return _store().custom_role(int(tail)) is not None


# ---- Groups ----


def list_groups():
    return _store().groups()


def create_group(name):
    return _store().create_group(name)


def delete_group(group_id):
    _store().delete_group(group_id)


def add_group_member(group_id, username):
    _store().add_group_member(group_id, username)


def remove_group_member(group_id, username):
    _store().remove_group_member(group_id, username)


def get_user_groups(username):
    return _store().groups_of(username)


# ---- Pools ----


def list_pools():
    return _store().pools()


def create_pool(name, description=""):
    return _store().create_pool(name, description)


def delete_pool(pool_id):
    _store().delete_pool(pool_id)


def add_pool_member(pool_id, vm_name):
    _store().add_pool_member(pool_id, vm_name)


def remove_pool_member(pool_id, vm_name):
    _store().remove_pool_member(pool_id, vm_name)


def rename_pool_member(old_vm_name, new_vm_name):
    """VM cloned/renamed: keep its pool membership under the new name."""
    _store().rename_pool_member(old_vm_name, new_vm_name)


def get_vm_pools(vm_name):
    return _store().pools_of(vm_name)


def remove_vm_from_all_pools(vm_name):
    """VM deleted: remove its pool membership (avoids orphaned entries pointing to a
    VM that no longer exists)."""
    _store().remove_vm_from_all_pools(vm_name)


# ---- ACL ----


def list_acl():
    acl = _store().acl()
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


def acl_for_object(resource_type, resource_id):
    """Assignments that give rights on one VM or container: its own, then (VMs only) those of the pools that
    contain it, each with `herite_de` naming the pool so the page can say where the right comes from."""
    entries = [
        dict(a, herite_de=None)
        for a in list_acl()
        if a["resource_type"] == resource_type and a["resource_id"] == resource_id
    ]
    if resource_type == "vm":
        pools = {p["id"]: p["name"] for p in list_pools() if resource_id in p["vms"]}
        for a in list_acl():
            if a["resource_type"] == "pool" and a["resource_id"].isdigit() and int(a["resource_id"]) in pools:
                entries.append(dict(a, herite_de=pools[int(a["resource_id"])]))
    return entries


def create_acl(subject_type, subject_id, role, resource_type, resource_id):
    if not role_exists(role):
        raise ValueError(f"Unknown role: {role}")
    return _store().create_acl(subject_type, subject_id, role, resource_type, resource_id)


def delete_acl(acl_id):
    _store().delete_acl(acl_id)


def delete_acl_for_vm(vm_name):
    _store().delete_acl_for_vm(vm_name)


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

    rows = _store().acl()

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

    rows = _store().acl("container")

    for row in rows:
        subject_ok = (row["subject_type"] == "user" and row["subject_id"] == user["username"]) or (
            row["subject_type"] == "group" and row["subject_id"].isdigit() and int(row["subject_id"]) in group_ids
        )
        if not subject_ok or row["resource_id"] != container_name:
            continue
        if privilege in get_role_privileges(row["role"]):
            return True
    return False
