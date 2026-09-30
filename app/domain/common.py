"""What every control plane resource shares, and the errors repositories raise instead of storage-specific ones."""

from pydantic import BaseModel


class ObjectMeta(BaseModel):
    """Identity and versioning of a resource. uid, generation and resource_version are filled from phase 2 (spec and
    status resources, optimistic concurrency); the SQLite repositories of phase 1 leave them empty."""

    uid: str | None = None
    generation: int | None = None
    resource_version: str | None = None


class RepositoryError(Exception):
    pass


class AlreadyExists(RepositoryError):
    pass


class NotFound(RepositoryError):
    pass
