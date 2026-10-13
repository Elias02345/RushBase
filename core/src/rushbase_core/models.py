"""Pydantic models for ``.rushbase/library.json``."""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SCHEMA_VERSION: Final = 1
"""Highest ``library.json`` schema version understood by this release."""


class ServerLease(BaseModel):
    """Marks which server instance currently owns (writes to) the library."""

    model_config = ConfigDict(frozen=True)

    holder: str = Field(min_length=1, description="Identifier of the server instance.")
    expires_at: AwareDatetime

    def is_expired(self, now: datetime | None = None) -> bool:
        """Return whether the lease has run out at ``now`` (default: current time)."""
        return (now or datetime.now(UTC)) >= self.expires_at


class LibraryManifest(BaseModel):
    """Content of ``.rushbase/library.json``."""

    model_config = ConfigDict(extra="forbid")

    library_id: UUID = Field(default_factory=uuid4)
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    server_lease: ServerLease | None = None
