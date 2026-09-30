"""The Protocols every storage backend implements. Asynchronous (maintainer decision, design section 15.1): the
etcd backend will be, and callers must not change when it arrives."""

from typing import Protocol

from app.domain.node import Maintenance, Node, NodeSpec


class NodeRepository(Protocol):
    async def get(self, name: str) -> Node | None: ...

    async def list(self) -> list[Node]: ...

    async def list_online(self) -> list[Node]: ...

    async def create(self, spec: NodeSpec, added_at: str, statut: str) -> Node:
        """Raises AlreadyExists when a node has that name."""

    async def delete(self, name: str) -> bool: ...

    async def update_status(self, node_id: int, statut: str, checked_at: str) -> str | None:
        """Record a check; returns the previous state (None when the node is gone)."""

    async def live(self, name: str | None = None) -> dict | None:
        """Latest live figures of one node ('local' = this host), or {name: figures} for every node."""

    async def write_live(self, rows: list[tuple]) -> None: ...

    async def get_maintenance(self, node: str) -> Maintenance | None: ...

    async def list_maintenance(self) -> list[Maintenance]: ...

    async def set_maintenance(self, node: str, username: str, started_at: str) -> None:
        """Idempotent: the first start time and author are kept."""

    async def clear_maintenance(self, node: str) -> bool: ...

    async def acquire_lease(self, name: str, ttl_seconds: int):
        """A node lease, renewed by the node's agent (phase 6: etcd). Not available before."""
