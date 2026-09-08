"""D-034 Phase 0: use-case artifact tracer tables.

Revision ID: v2_143
Revises: v2_141

Adds the three tables backing the portable BNK use-case artifact tracer
(docs/adr/D-034):
  - usecase_artifacts: named, mutable container (rename/describe only).
  - usecase_artifact_versions: immutable once created; cr_templates +
    param_schema JSON blobs; unique (artifact_id, version).
  - usecase_applications: binding of an artifact version + param_values to a
    cluster, so drift always compares against the exact desired-state that
    was applied.

Note: down_revision is v2_141, not v2_142. v2_142 is owned by the in-flight
wave-1 benchmarks branch (not yet merged) — both parent v2_141, creating
parallel heads that the project's stacked-migration merge rule resolves at
merge time (serial merge or a merge revision).
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_143"
down_revision = "v2_141"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usecase_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_usecase_artifacts_id", "usecase_artifacts", ["id"])

    op.create_table(
        "usecase_artifact_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_id", sa.Integer(),
            sa.ForeignKey("usecase_artifacts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("matching_bnk_version", sa.String(64), nullable=True),
        sa.Column("cr_templates", sa.JSON(), nullable=False),
        sa.Column("param_schema", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column(
            "source_cluster_id", sa.Integer(),
            sa.ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("artifact_id", "version", name="uq_usecase_artifact_version"),
    )
    op.create_index("ix_usecase_artifact_versions_id", "usecase_artifact_versions", ["id"])
    op.create_index(
        "idx_usecase_artifact_version_artifact", "usecase_artifact_versions", ["artifact_id"]
    )
    op.create_index(
        "idx_usecase_artifact_version_content_hash", "usecase_artifact_versions", ["content_hash"]
    )

    op.create_table(
        "usecase_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_version_id", sa.Integer(),
            sa.ForeignKey("usecase_artifact_versions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "cluster_id", sa.Integer(),
            sa.ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("param_values", sa.JSON(), nullable=False),
        sa.Column("applied_by", sa.String(255), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_usecase_applications_id", "usecase_applications", ["id"])
    op.create_index(
        "idx_usecase_application_version", "usecase_applications", ["artifact_version_id"]
    )
    op.create_index("idx_usecase_application_cluster", "usecase_applications", ["cluster_id"])


def downgrade() -> None:
    op.drop_index("idx_usecase_application_cluster", table_name="usecase_applications")
    op.drop_index("idx_usecase_application_version", table_name="usecase_applications")
    op.drop_index("ix_usecase_applications_id", table_name="usecase_applications")
    op.drop_table("usecase_applications")

    op.drop_index("idx_usecase_artifact_version_content_hash", table_name="usecase_artifact_versions")
    op.drop_index("idx_usecase_artifact_version_artifact", table_name="usecase_artifact_versions")
    op.drop_index("ix_usecase_artifact_versions_id", table_name="usecase_artifact_versions")
    op.drop_table("usecase_artifact_versions")

    op.drop_index("ix_usecase_artifacts_id", table_name="usecase_artifacts")
    op.drop_table("usecase_artifacts")
