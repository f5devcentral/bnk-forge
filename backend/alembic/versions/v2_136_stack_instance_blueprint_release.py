"""StackInstance gains blueprint_release_id; template_id made nullable.

Revision ID: v2_136
Revises: v2_135
Create Date: 2026-06-14

Allows a StackInstance to be created from an imported BlueprintRelease rather
than a StackTemplate.  Both columns are now optional so that blueprint-backed
and template-backed stacks can coexist.

Changes:
  - stack_instances.template_id: NOT NULL → NULL
  - stack_instances.blueprint_release_id: new nullable INTEGER FK → blueprint_releases.id
    ON DELETE SET NULL, indexed as idx_stack_instance_blueprint_release
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_136"
down_revision = "v2_135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make template_id nullable — existing rows keep their value, no backfill needed.
    op.alter_column("stack_instances", "template_id", existing_type=sa.Integer(), nullable=True)

    # Add blueprint_release_id FK column.
    op.add_column(
        "stack_instances",
        sa.Column("blueprint_release_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_stack_instances_blueprint_release_id",
        "stack_instances",
        "blueprint_releases",
        ["blueprint_release_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_stack_instance_blueprint_release",
        "stack_instances",
        ["blueprint_release_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    null_template_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM stack_instances WHERE template_id IS NULL")
    ).scalar()
    if null_template_count:
        raise RuntimeError(
            "Cannot downgrade v2_136: stack_instances contains rows with NULL template_id. "
            "Delete or migrate blueprint-backed stack instances before downgrade."
        )

    op.drop_index("idx_stack_instance_blueprint_release", table_name="stack_instances")
    op.drop_constraint("fk_stack_instances_blueprint_release_id", "stack_instances", type_="foreignkey")
    op.drop_column("stack_instances", "blueprint_release_id")
    op.alter_column("stack_instances", "template_id", existing_type=sa.Integer(), nullable=False)
