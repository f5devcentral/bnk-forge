"""Response models for catalog pruning.

Shared by both prune routes (module sources and blueprint sources) so the two
report an identical shape — the service returns the same ``PruneResult`` for
either, and a caller should not have to care which catalog it pruned.
"""

from typing import Literal

from pydantic import BaseModel, Field


class PruneRequest(BaseModel):
    """How much of a source's version history to retire.

    Shared by both prune routes. They were byte-identical inline models in the
    two route files, which is the "schemas live in TWO places" trap in
    AGENTS.md — and it would have produced two separate OpenAPI schema names
    free to drift apart.
    """

    keep: int = Field(
        default=1, ge=1, le=50,
        description="Newest versions to keep per module path / blueprint id",
    )
    delete: bool = Field(
        default=False,
        description="Remove unreferenced rows outright instead of deactivating",
    )
    dry_run: bool = Field(
        default=False, description="Report what would happen and change nothing"
    )
    include_in_use: bool = Field(
        default=False,
        description=(
            "Also deactivate versions something is deployed from. "
            "They are still never deleted."
        ),
    )


class PruneItemResponse(BaseModel):
    """What happened to one catalog version."""

    identity: str = Field(description="Module path, or blueprint id")
    version: str = Field(description="The version or release considered")
    action: Literal["kept", "deactivated", "deleted", "in_use"] = Field(
        description=(
            "kept — within the newest `keep`, or already inactive; "
            "deactivated — hidden but still resolvable for anything pinned to it; "
            "deleted — row removed, only ever one nothing references; "
            "in_use — something is deployed from it, so it was left untouched"
        )
    )
    reason: str = Field(default="", description="Why, when the action needs explaining")


class PruneResponse(BaseModel):
    """The full plan, and what was carried out unless ``dry_run``."""

    source_id: int
    dry_run: bool = Field(description="True when nothing was changed")
    keep: int = Field(description="Newest versions retained per module path / blueprint id")
    counts: dict[str, int] = Field(
        default_factory=dict, description="Item count per action"
    )
    items: list[PruneItemResponse] = Field(default_factory=list)
