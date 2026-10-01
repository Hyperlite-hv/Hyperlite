"""Renaming a VM, a container or a node: what Hyperlite keeps under the old name follows it.

libvirt knows a VM or a container by its name, and so does Hyperlite's database: its SSH user, backups and their
schedule, pools and permissions, HA protection, start at boot, notes and tags, metrics history... A rename moves all
of it in one transaction, so the object keeps its settings and its history. The audit log and finished tasks are
history and keep the name the object had then.

A node is only a name in this database (the machine itself keeps its host name): the rename moves the rows that name
it, and the name of the key it uses to reach this host back for migrations (app/core/cluster.py::ensure_reverse_trust).
"""

import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

LOCAL = "local"


def _store():
    from app.repositories import registry

    return registry.renames().sync


def _renamed_xml(xml, new):
    """HA's cached copy of a VM's definition (app/core/ha.py), with the new name: it is what would redefine the VM on
    another node, and the periodic refresh may not have run yet."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return xml
    name = root.find("name")
    if name is not None:
        name.text = new
    return ET.tostring(root, encoding="unicode")


def vm_records(old, new, node=None):
    """Move a VM's records from `old` to `new`."""
    # Metrics: a local VM is sampled as "<vm>", a VM of a registered node as "<node>:<vm>" (app/core/metrics.py).
    prefix = f"{node}:" if node and node != LOCAL else ""
    _store().vm_records(old, new, node or LOCAL, prefix, _renamed_xml)


def container_records(old, new):
    _store().container_records(old, new)


def node_records(old, new):
    _store().node_records(old, new)


def storage_pool_records(old, new, node_key):
    """A storage pool renamed on one node: the containers stored in it (this host) and its usage history."""
    _store().storage_pool_records(old, new, node_key)


def network_records(old, new):
    """A network renamed on this host: its firewall rules, the application containers and Kubernetes clusters on it."""
    _store().network_records(old, new)


def vm_in_k8s_cluster(name):
    """The Kubernetes cluster a VM belongs to, or None: those VMs are found by their name (app/core/k8s_cluster.py)."""
    return _store().vm_in_k8s_cluster(name)


class LabelTaken(ValueError):
    pass


def rename_label(kind, object_id, new, owner=None):
    """Rename a pool, group, custom role, job or API token (objects known by an id, whose name is only a label);
    its old name, or None when there is no such object (for `owner`, when given). Raises LabelTaken when another
    one already has the name (the tables require unique names)."""
    from app.repositories.sqlite.renames import TAKEN

    old = _store().rename_label(kind, object_id, new, owner)
    if old is TAKEN:
        raise LabelTaken(f"The name '{new}' is already used")
    return old
