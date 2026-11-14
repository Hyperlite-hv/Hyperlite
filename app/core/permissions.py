"""Granular permissions: user groups + VM pools + scoped roles +
assignments (ACLs), on top of the two existing global roles (admin/viewer,
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
    # Commands run inside the guest through its agent, as the agent's user (root, SYSTEM): as strong as a console
    # signed in as an administrator, so in no predefined role.
    "vm.exec": "Run commands inside the VM through the guest agent",
    "vm.snapshot": "Snapshots (create / restore / delete)",
    "vm.resize": "Resize (CPU / RAM / disk)",
    "vm.hardware": "Hardware (disks, network interfaces, CD drive)",
    "vm.clone": "Clone the VM (creates a new VM and consumes disk space)",
    "vm.options": "Options (start at boot, notes and tags)",
    # The steps of a VM's creation, each on what it uses: an
    # account that is not an administrator (a Terraform pipeline's token) creates VMs only in its pool, on its
    # storage and its networks.
    "vm.allocate": "Create and delete VMs (assigned on a pool: the VMs go in that pool)",
    "storage.allocate": "Put VM disks on a storage pool (assigned on that storage pool)",
    "network.assign": "Connect VMs to a network (assigned on that network)",
    # LXC containers: a catalog distinct from vm.* (a different resource, even
    # though `has_container_privilege` reuses the SAME predefined roles below).
    "container.view": "View (state, IP)",
    "container.power": "Start / stop",
    "container.console": "Console log (the web terminal is for administrators)",
    "container.options": "Options (notes and tags)",
    # Another cluster's copy of a backup store pulls it with this: a
    # dedicated account's API token reads the store's chunks and indexes, nothing else, and deletes nothing.
    "backup_store.read": "Read a backup store, to copy it from another site (assigned on that store)",
    # Area privileges (docs/design/access-2.md, section 2): held on the cluster or a node, they open a whole area of
    # the console that was an administrator's. AREA_REACH says which count on a node too.
    "node.view": "Nodes: read host status, services and settings",
    "node.manage": "Nodes: host settings, maintenance, services",
    "storage.view": "Storage: read storage pools and replication",
    "storage.manage": "Storage: create, change and remove storage pools, replication",
    "network.view": "Networks: read networks, bridges and firewall",
    "network.manage": "Networks: create, change and remove networks, bridges and firewall rules",
    "backup.view": "Backups: read jobs, groups, stores and restores",
    "backup.manage": "Backups: jobs, groups, stores and restores",
    "alert.view": "Alerts: read alert rules, notification channels and reports",
    "alert.manage": "Alerts: alert rules, notification channels, reports and metric servers",
    "audit.view": "Journal: read the activity journal",
    "update.view": "Updates: read status, history and preflight",
    "update.apply": "Updates: check, download and install updates",
    "cluster.view": "Cluster: read HA, load balancing and cluster settings",
    "cluster.manage": "Cluster: HA, load balancing and cluster settings",
    "access.view": "Access: read assignments, groups and roles in the perimeter",
    "access.manage": "Access: give and remove assignments in the perimeter",
}

AREAS = ("node", "storage", "network", "backup", "alert", "audit", "update", "cluster", "access")
# The areas that are the whole cluster's configuration (replicated tables): held on a node they would let that node's
# delegate change every other node's, so only the cluster perimeter grants them.
CLUSTER_ONLY_AREAS = {"backup", "alert", "audit", "cluster"}
_VIEW = {p for p in ALL_PRIVILEGES if p.endswith(".view")}
_EVERYTHING = set(ALL_PRIVILEGES) - {"vm.exec"}  # vm.exec stays explicit: a root shell in every guest

# Predefined, scoped roles that can be assigned through an ACL (distinct from
# the global admin/viewer roles): convenient shortcuts over the same
# privilege catalog as the custom roles below, not a separate mechanism.
ROLES = {
    "drive": {
        "label": "Reader",
        "description": "View (state, metrics, journal) on the assigned resource.",
        "privileges": {"vm.view", "container.view"},
    },
    "operator": {
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
    "manager": {
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
    "store_reader": {
        "label": "Backup store reader",
        "description": "Reads the assigned backup store to copy it from another site; nothing else, and deletes nothing.",
        "privileges": {"backup_store.read"},
    },
    # Wide roles, meant for a node, a pool or the cluster (docs/design/access-2.md, section 2).
    "steward": {
        "label": "Steward",
        "description": "Everything in the perimeter, including giving access to others.",
        "privileges": _EVERYTHING,
    },
    "builder": {
        "label": "Builder",
        "description": "Everything in the perimeter except access: creates, changes and removes, gives no rights.",
        "privileges": {p for p in _EVERYTHING if not p.startswith("access.")},
    },
    "observer": {
        "label": "Observer",
        "description": "Reads everything in the perimeter, changes nothing.",
        "privileges": _VIEW | {"container.view"},
    },
    "access_delegate": {
        "label": "Access delegate",
        "description": "Gives and removes assignments in the perimeter, without other rights.",
        "privileges": {"access.view", "access.manage"},
    },
    "backup_keeper": {
        "label": "Backup keeper",
        "description": "Backup jobs, stores and restores, and snapshots of the VMs in the perimeter.",
        "privileges": {"backup.view", "backup.manage", "vm.view", "vm.snapshot", "backup_store.read"},
    },
    "watcher": {
        "label": "Watcher",
        "description": "Alert rules, notification channels and reports, and reading everything to follow it.",
        "privileges": _VIEW - {"access.view"} | {"alert.manage", "container.view"},
    },
    "maintainer": {
        "label": "Maintainer",
        "description": "Updates and maintenance of the nodes in the perimeter.",
        "privileges": {"update.view", "update.apply", "node.view", "node.manage"},
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


def _check_privileges(privileges):
    invalid = set(privileges) - set(ALL_PRIVILEGES)
    if invalid:
        raise ValueError(f"Unknown privileges: {', '.join(sorted(invalid))}")
    if not privileges:
        raise ValueError("Choose at least one privilege")


def create_custom_role(name, privileges):
    _check_privileges(privileges)
    return _store().create_custom_role(name, ",".join(privileges))


def set_custom_role_privileges(role_id, privileges):
    """Change what a role allows, keeping its assignments (deleting and creating it again would drop them). False
    when there is no such role."""
    _check_privileges(privileges)
    return _store().set_custom_role_privileges(role_id, ",".join(privileges))


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


def set_pool_description(pool_id, description):
    return _store().set_pool_description(pool_id, description)


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

# What an assignment can be given on: a VM, a pool of VMs, a container, a storage pool, a network, a backup store.
RESOURCE_TYPES = ("vm", "pool", "container", "storage", "network", "backup_store", "node", "cluster")
CLUSTER = ("cluster", "*")  # the one cluster perimeter


def list_acl():
    acl = _store().acl()
    terms = _store().terms()
    for a in acl:
        t = terms.get(a["id"]) or {}
        a.update({k: t.get(k) for k in ("expires_at", "tag", "reason", "granted_by", "origin")})
    groups = {g["id"]: g["name"] for g in list_groups()}
    pools = {p["id"]: p["name"] for p in list_pools()}
    for a in acl:
        if a["subject_type"] == "group":
            a["subject_label"] = groups.get(int(a["subject_id"]), f"group#{a['subject_id']}")
        else:
            a["subject_label"] = a["subject_id"]
        if a["resource_type"] == "pool":
            a["resource_label"] = pools.get(int(a["resource_id"]), f"pool#{a['resource_id']}")
        elif a["resource_type"] == "cluster":
            a["resource_label"] = "cluster"
        else:
            a["resource_label"] = a["resource_id"]
    return acl


def acl_for_object(resource_type, resource_id):
    """Assignments that give rights on one object: its own, then those of the perimeters that contain it (a VM's
    pools, the node, the cluster), each with `inherited_from` naming that perimeter (None for its own) and
    `inherited_type` its kind, so the page can say where the right comes from."""
    acl = list_acl()
    entries = []
    for kind, ident in perimeters_of(resource_type, resource_id):
        own = (kind, ident) == (resource_type, str(resource_id))
        for a in acl:
            if (a["resource_type"], a["resource_id"]) == (kind, ident):
                entries.append(
                    dict(a, inherited_from=None if own else a["resource_label"], inherited_type=None if own else kind)
                )
    return entries


def create_acl(subject_type, subject_id, role, resource_type, resource_id):
    if not role_exists(role):
        raise ValueError(f"Unknown role: {role}")
    return _store().create_acl(subject_type, subject_id, role, resource_type, resource_id)


def delete_acl(acl_id):
    _store().delete_acl(acl_id)


def delete_acl_for_vm(vm_name):
    _store().delete_acl_for_vm(vm_name)


def delete_acl_for_user(username):
    """A deleted account's rights go with it: they were kept, and given to the next account created under that name."""
    for entry in _store().acl():
        if entry["subject_type"] == "user" and entry["subject_id"] == username:
            _store().delete_acl(entry["id"])


# ---- Effective permission check ----


def _local_node():
    from app.core import self_node

    return self_node.name()


def perimeters_of(resource_type, resource_id):
    """The perimeters whose assignments hold on that object, the object first (docs/design/access-2.md, section 1):
    a VM's pools, the node that answers (every route about an object runs on the node that hosts it) and the
    cluster."""
    resource_id = str(resource_id)
    if resource_type == "cluster":
        return [CLUSTER]
    found = [(resource_type, resource_id)]
    if resource_type == "vm":
        found += [("pool", str(p)) for p in get_vm_pools(resource_id)]
    if resource_type in ("vm", "container", "storage", "network", "backup_store"):
        found.append(("node", _local_node()))
    found.append(CLUSTER)
    return found


def _subject_matches(row, username, group_ids):
    if row["subject_type"] == "user":
        return row["subject_id"] == username
    return row["subject_id"].isdigit() and int(row["subject_id"]) in group_ids


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _ended(terms, now=None):
    from datetime import datetime

    if not terms or not terms.get("expires_at"):
        return False
    try:
        return datetime.fromisoformat(terms["expires_at"]) <= (now or _now())
    except ValueError:
        return True  # an end that cannot be read: the assignment grants nothing rather than forever


def _in_force(row, terms, target):
    """An assignment grants while it has not ended (dated rights) and, when it carries a tag restriction, only on a
    VM or container with that tag (docs/design/access-2.md, sections 6 and 8)."""
    t = terms.get(row["id"])
    if not t:
        return True
    if _ended(t):
        return False
    if t.get("tag"):
        if target is None or target[0] not in ("vm", "container"):
            return False
        from app.core import object_meta

        return t["tag"] in object_meta.get(target[0], target[1])["tags"]
    return True


def granting(user, perimeters, privilege, rows=None):
    """The assignments in force that give `user` `privilege` on one of `perimeters` (pairs of resource type and id,
    the object itself first), directly or through a group."""
    group_ids = set(get_user_groups(user["username"]))
    perimeters = [(t, str(i)) for t, i in perimeters]
    wanted = set(perimeters)
    target = perimeters[0] if perimeters and perimeters[0][0] in ("vm", "container") else None
    terms = _store().terms()
    return [
        row
        for row in (_store().acl() if rows is None else rows)
        if (row["resource_type"], row["resource_id"]) in wanted
        and _subject_matches(row, user["username"], group_ids)
        and privilege in get_role_privileges(row["role"])
        and _in_force(row, terms, target)
    ]


def _holds(user, perimeters, privilege, viewer=()):
    """`user` holds `privilege` on the first of `perimeters`: an administrator always, an observer the `viewer`
    privileges, anyone through an assignment in force. A delegated API token (user["token_scope"]) holds only what
    its role gives on its perimeter, and only what its owner holds (docs/design/access-2.md, section 7)."""
    scope = user.get("token_scope")
    if scope:
        on = {(t, str(i)) for t, i in perimeters}
        if (
            privilege not in get_role_privileges(scope["role"])
            or (scope["resource_type"], scope["resource_id"]) not in on
        ):
            return False
        owner = {k: v for k, v in user.items() if k != "token_scope"}
        owner["role"] = user["owner_role"]
        return _holds(owner, perimeters, privilege, viewer)
    if user["role"] == "admin":
        return True
    if user["role"] == "viewer" and privilege in viewer:
        return True
    return bool(granting(user, perimeters, privilege))


def self_service_allows(user, role, resource_type, resource_id, expires_at):
    """True when `user` (or one of its groups) is eligible to take `role` on that perimeter at once, until
    `expires_at`, within the eligibility's longest time."""
    from datetime import datetime, timedelta

    try:
        end = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return False
    return any(
        e["approval"] == "auto" and end <= _now() + timedelta(minutes=e["max_minutes"], seconds=60)
        for e in eligible_for(user, role, resource_type, resource_id)
    )


def eligible_for(user, role, resource_type, resource_id):
    """The eligibilities that let `user` ask for `role` on that perimeter."""
    group_ids = set(get_user_groups(user["username"]))
    return [
        e
        for e in _store().eligibility()
        if _subject_matches(e, user["username"], group_ids)
        and e["role"] == role
        and (e["resource_type"], e["resource_id"]) == (resource_type, str(resource_id))
    ]


def list_eligibility():
    return _store().eligibility()


def create_eligibility(subject_type, subject_id, role, resource_type, resource_id, max_minutes, approval, by):
    if not role_exists(role):
        raise ValueError(f"Unknown role: {role}")
    return _store().create_eligibility(
        subject_type, subject_id, role, resource_type, str(resource_id), max_minutes, approval, by, _now().isoformat()
    )


def delete_eligibility(eligibility_id):
    _store().delete_eligibility(eligibility_id)


def create_dated_acl(subject_type, subject_id, role, resource_type, resource_id, expires_at, tag, reason, by, origin):
    """An assignment with terms (an end, a tag restriction, a reason), created with them in one write."""
    if not role_exists(role):
        raise ValueError(f"Unknown role: {role}")
    return _store().create_acl_with_terms(
        subject_type, subject_id, role, resource_type, str(resource_id), expires_at, tag, reason or "", by,
        _now().isoformat(), origin,
    )  # fmt: skip


def sweep_ended():
    """Remove the assignments whose end has passed; returns them (each with its terms), for the journal."""
    terms = _store().terms()
    now = _now()
    ended = []
    for row in _store().acl():
        t = terms.get(row["id"])
        if t and _ended(t, now):
            _store().delete_acl(row["id"])
            ended.append({**row, "terms": t})
    return ended


def has_privilege(user, vm_name, privilege):
    """True if `user` has the requested privilege on `vm_name`, through their global
    role (admin/viewer) OR an assignment (direct or through a group) on this VM, a pool that contains it, its node
    or the cluster."""
    # Observers keep the historical read + VNC console at the global read level (not the SSH terminal).
    return _holds(user, perimeters_of("vm", vm_name), privilege, viewer={"vm.view", "vm.console"})


def has_scoped_privilege(user, resource_type, resource_id, privilege):
    """True if `user` holds `privilege` through an assignment on that object (a pool by its id, a storage pool or
    a network by its name, a node, the cluster) or a perimeter that contains it, directly or through a group. An
    administrator holds every privilege."""
    return _holds(user, perimeters_of(resource_type, resource_id), privilege)


def has_area_privilege(user, privilege):
    """True if `user` holds an area privilege (node.manage, backup.view...) where this node answers: on the cluster,
    or on this node for the areas a node may hold (CLUSTER_ONLY_AREAS)."""
    area = privilege.split(".", 1)[0]
    perimeters = [CLUSTER] if area in CLUSTER_ONLY_AREAS else [("node", _local_node()), CLUSTER]
    return _holds(user, perimeters, privilege)


def effective(user):
    """What `user` holds where this node answers: its area privileges, and per perimeter the privileges of its
    assignments there, for the dashboard (GET /access/me)."""
    if user["role"] == "admin":
        return {"admin": True, "areas": sorted(p for p in ALL_PRIVILEGES if p.split(".")[0] in AREAS), "perimeters": []}
    scope = user.get("token_scope")
    if scope:
        return {
            "admin": False,
            "areas": sorted(p for p in ALL_PRIVILEGES if p.split(".")[0] in AREAS and has_area_privilege(user, p)),
            "perimeters": [{**scope, "privileges": sorted(get_role_privileges(scope["role"]))}],
            "delegated": True,
        }
    terms = _store().terms()
    group_ids = set(get_user_groups(user["username"]))
    held = {}
    for row in _store().acl():
        if _subject_matches(row, user["username"], group_ids) and not _ended(terms.get(row["id"])):
            key = (row["resource_type"], row["resource_id"])
            held.setdefault(key, set()).update(get_role_privileges(row["role"]))
    areas = sorted(p for p in ALL_PRIVILEGES if p.split(".")[0] in AREAS and has_area_privilege(user, p))
    return {
        "admin": False,
        "areas": areas,
        "perimeters": [
            {"resource_type": t, "resource_id": i, "privileges": sorted(privs)}
            for (t, i), privs in sorted(held.items())
        ],
    }


def explain(user, resource_type, resource_id):
    """Each privilege `user` holds on that object, with the assignments that grant it (GET /access/explain): the
    answer of "who can do what" for one account."""
    perimeters = perimeters_of(resource_type, resource_id)
    rows = _store().acl()
    terms = _store().terms()
    out = []
    for privilege in ALL_PRIVILEGES:
        if user["role"] == "admin":
            out.append({"privilege": privilege, "via": [{"global": "admin"}]})
            continue
        via = [
            {
                "acl_id": r["id"],
                "role": r["role"],
                "resource_type": r["resource_type"],
                "resource_id": r["resource_id"],
                "subject_type": r["subject_type"],
                "subject_id": r["subject_id"],
                "expires_at": (terms.get(r["id"]) or {}).get("expires_at"),
                "tag": (terms.get(r["id"]) or {}).get("tag"),
            }
            for r in granting(user, perimeters, privilege, rows)
        ]
        if user["role"] == "viewer" and privilege in {"vm.view", "vm.console", "container.view"}:
            via.insert(0, {"global": "viewer"})
        if via:
            out.append({"privilege": privilege, "via": via})
    return out


def grant_refusal(user, subject_type, subject_id, role, resource_type, resource_id):
    """Why `user` may not give (or remove) that assignment, or None (docs/design/access-2.md, section 2). An
    administrator may always. A delegate (access.manage on a perimeter) may on that perimeter and below it, except
    the cluster, never to itself or a group it belongs to, and gives another delegate only from a wider perimeter."""
    if user["role"] == "admin":
        return None
    if resource_type == "cluster":
        return "Only an administrator gives rights on the whole cluster"
    if (subject_type == "user" and subject_id == user["username"]) or (
        subject_type == "group"
        and str(subject_id).isdigit()
        and int(subject_id) in set(get_user_groups(user["username"]))
    ):
        return "Rights are given to you by someone else: not to yourself or a group you belong to"
    target = perimeters_of(resource_type, resource_id)
    holding = granting(user, target, "access.manage") if _holds(user, target, "access.manage") else []
    if not holding:
        return f"Insufficient rights on {resource_type} '{resource_id}' (required privilege: access.manage)"
    if "access.manage" in get_role_privileges(role) and all(
        (r["resource_type"], r["resource_id"]) == (resource_type, str(resource_id)) for r in holding
    ):
        return "A delegate gives another delegate only from a wider perimeter than the one they share"
    return None


def holds_anywhere(user, privilege):
    """True if one of `user`'s assignments, on any perimeter, gives `privilege`."""
    scope = user.get("token_scope")
    if scope:
        owner = {**{k: v for k, v in user.items() if k != "token_scope"}, "role": user["owner_role"]}
        return privilege in get_role_privileges(scope["role"]) and holds_anywhere(owner, privilege)
    if user["role"] == "admin":
        return True
    group_ids = set(get_user_groups(user["username"]))
    return any(
        _subject_matches(r, user["username"], group_ids) and privilege in get_role_privileges(r["role"])
        for r in _store().acl()
    )


def may_view_access(user, resource_type, resource_id):
    return _holds(user, perimeters_of(resource_type, resource_id), "access.view")


def refuse_unless(user, resource_type, resource_id, privilege, what):
    """403 unless `user` holds `privilege` on that storage pool or network (has_scoped_privilege); `what` names it in
    the refusal ("storage pool 'fast'"). For the routes that put a disk or an interface on an existing VM."""
    from fastapi import HTTPException

    if not has_scoped_privilege(user, resource_type, resource_id, privilege):
        raise HTTPException(status_code=403, detail=f"Insufficient rights on {what} (required privilege: {privilege})")


def allocation_refusal(user, resource_pool, storage_pools, networks):
    """Why `user` may not create a VM in the pool `resource_pool` (an id, or None), with disks on `storage_pools` and
    interfaces on `networks`; None when it may. An administrator may always; anyone else needs vm.allocate on the
    pool, storage.allocate on each storage pool and network.assign on each network."""
    if user["role"] == "admin":
        return None
    if resource_pool is None:
        return "Choose the pool the VM goes in (resource_pool): only an administrator creates VMs outside a pool"
    if not has_scoped_privilege(user, "pool", resource_pool, "vm.allocate"):
        return f"Insufficient rights on pool {resource_pool} (required privilege: vm.allocate)"
    for name in sorted(set(storage_pools)):
        if not has_scoped_privilege(user, "storage", name, "storage.allocate"):
            return f"Insufficient rights on storage pool '{name}' (required privilege: storage.allocate)"
    for name in sorted(set(networks)):
        if not has_scoped_privilege(user, "network", name, "network.assign"):
            return f"Insufficient rights on network '{name}' (required privilege: network.assign)"
    return None


def has_container_privilege(user, container_name, privilege):
    """Equivalent of has_privilege() for LXC containers: the same role catalog, with resource_type='container' in
    the assignments instead of 'vm', and the node and the cluster above it. Containers are not members of pools."""
    # Same principle as vm.view: global read access for observers, not the terminal.
    return _holds(user, perimeters_of("container", container_name), privilege, viewer={"container.view"})
