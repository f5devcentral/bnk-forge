"""D-033 PR-1: multi-version module catalog identity columns.

Revision ID: v2_141
Revises: v2_140
Create Date: 2026-07-17

Adds:
  - module_library.content_sha256: nullable String(64). Canonical hash of the
    synced manifest content. NULL marks a legacy (pre-D-033) row, which the
    sync may grandfather-update in place exactly once (setting the hash);
    hashed rows are structurally immutable (model-level guard).
  - module_library.is_latest: Boolean NOT NULL default true. Recomputed per
    (module_source_id, path) after every sync.
  - Unique constraint (module_source_id, path, version) — module identity now
    includes the version, so one row per version accumulates instead of the
    sync overwriting a single row per path.
  - Index (path, is_latest) for latest-version resolution.

Backfill: every existing row is the sole version of its path → is_latest=true
(column default). content_sha256 stays NULL (grandfathered). Defensive dedupe:
if any (module_source_id, path, version) group somehow holds duplicates, detach
older duplicates' version with a '+dup<id>' suffix rather than deleting rows
that project_modules may reference. The dedupe touches ONLY the version column
— is_active/is_latest are left as-is, so a dup group can briefly hold two
is_latest=true rows until the next sync's recompute_is_latest pass corrects it.

Caveats:
  - Downgrade is schema-only, not data-reversible: '+dup<id>' suffixes and
    multi-version rows persist, leaving pre-D-033 code back on arbitrary
    .first() resolution over them.
  - Postgres treats NULLs as distinct in unique constraints, so the constraint
    does not police future NULL-version duplicates — mitigated by the sync
    upsert matching NULL versions with an IS NULL filter.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_141"
down_revision = "v2_140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "module_library",
        sa.Column("content_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "module_library",
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    # Defensive dedupe before the unique constraint: suffix older duplicates'
    # version instead of deleting (project_modules FK may reference them).
    # A row whose id is not the MAX(id) of its (module_source_id, path, version)
    # group is by definition a non-newest duplicate — no second predicate needed.
    # (A row-value IN predicate here would silently skip groups with NULL
    # version/module_source_id, exactly the legacy groups this targets.)
    op.execute(
        """
        UPDATE module_library
        SET version = COALESCE(version, '') || '+dup' || id
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM module_library
            GROUP BY module_source_id, path, version
        )
        """
    )

    op.create_unique_constraint(
        "uq_module_library_source_path_version",
        "module_library",
        ["module_source_id", "path", "version"],
    )
    op.create_index(
        "idx_module_library_path_latest",
        "module_library",
        ["path", "is_latest"],
    )


def downgrade() -> None:
    op.drop_index("idx_module_library_path_latest", table_name="module_library")
    op.drop_constraint(
        "uq_module_library_source_path_version", "module_library", type_="unique"
    )
    op.drop_column("module_library", "is_latest")
    op.drop_column("module_library", "content_sha256")
