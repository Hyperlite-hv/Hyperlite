"""A registered node: what an administrator declared (spec), what Hyperlite observed (status), and its maintenance.

The local host is not a Node: it is the machine running Hyperlite, named "local" everywhere. to_wire() gives the
exact JSON the current API returns (French wire identifiers, see CLAUDE.md), so moving the storage behind a
repository changes no response.
"""

from pydantic import BaseModel, Field

from app.domain.common import ObjectMeta

ONLINE, OFFLINE, UNKNOWN = "online", "offline", "unknown"


class NodeSpec(BaseModel):
    name: str
    hostname: str
    ssh_user: str = "root"
    ssh_port: int = Field(22, ge=1, le=65535)


class NodeStatus(BaseModel):
    status: str = UNKNOWN
    last_verification: str | None = None


class Node(BaseModel):
    id: int
    spec: NodeSpec
    status: NodeStatus
    added_at: str
    metadata: ObjectMeta = Field(default_factory=ObjectMeta)

    @property
    def name(self):
        return self.spec.name

    def to_wire(self):
        return {
            "id": self.id,
            "name": self.spec.name,
            "hostname": self.spec.hostname,
            "ssh_user": self.spec.ssh_user,
            "ssh_port": self.spec.ssh_port,
            "status": self.status.status,
            "last_verification": self.status.last_verification,
            "added_at": self.added_at,
        }


class Maintenance(BaseModel):
    node: str  # "local" or a registered node's name
    started_by: str
    started_at: str

    def to_wire(self):
        return self.model_dump()
