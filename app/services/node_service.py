"""Registered nodes: listing with their live figures, lookup, removal, rename and their state for HA.

Registration and removal also touch SSH trust (app/core/cluster.py); those steps block on the network and run in a
worker thread, the storage goes through the NodeRepository.
"""

import asyncio

from app.domain.node import ONLINE, UNKNOWN
from app.repositories import registry

LOCAL = "local"


class NodeNotFound(LookupError):
    def __init__(self, name):
        super().__init__(f"Node '{name}' not found")


class NodeNameTaken(ValueError):
    def __init__(self, name):
        super().__init__(f"A node named '{name}' already exists")


async def list_with_live():
    """Every registered node as the API returns it, with its latest live figures (None until measured)."""
    repo = registry.nodes()
    nodes, live = await asyncio.gather(repo.list(), repo.live())
    return [{**n.to_wire(), "live": live.get(n.name)} for n in nodes]


async def get(name):
    node = await registry.nodes().get(name)
    if node is None:
        raise NodeNotFound(name)
    return node


async def exists(name):
    return await registry.nodes().get(name) is not None


async def live(name):
    return await registry.nodes().live(name)


async def statut(node_label):
    """A node's last known state; the local host, which runs Hyperlite, is online by definition."""
    if node_label == LOCAL:
        return ONLINE
    node = await registry.nodes().get(node_label)
    return node.status.statut if node else UNKNOWN


async def remove(name, username):
    from app.core import cluster, maintenance

    await get(name)
    await asyncio.to_thread(cluster.remove_node, name, username)
    await asyncio.to_thread(maintenance.leave, name)


async def check_rename(name, new):
    """404 when the node does not exist, NodeNameTaken when the new name is used."""
    await get(name)
    if await exists(new):
        raise NodeNameTaken(new)


async def wire_with_live(name):
    node = await get(name)
    return {**node.to_wire(), "live": await live(name)}
