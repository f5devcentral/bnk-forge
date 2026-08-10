"""Heal schema objects a stamped-head install can never receive.

Revision ID: v2_152
Revises: v2_151

Fresh installs are provisioned by ``init_db.py``: ``create_all`` from the ORM,
then ``alembic stamp head``. That records "every revision up to head has run"
while the schema is actually whatever the ORM looked like *in that build*. The
two agree only for as long as the ORM and the migration chain stay in step, and
they have not:

  * An object a migration creates BELOW the stamp, which the ORM of that build
    did not declare, is never created — and never will be. The stamp says its
    migration already ran, so no upgrade will replay it. It is silently absent
    until some later build's ORM SELECTs it.

  * An object the ORM declares whose migration sits ABOVE the stamp is built by
    create_all anyway, then collides when the upgrade replays that migration.
    (Handled at the source in v2_138, which is now idempotent.)

``stack_instances.blueprint_release_id`` is the first case. It is added by
v2_136, below the v2_137 that installs stamp at, and the ORM did not declare it
then — so on every such stack the column is missing while alembic reports it
applied. The backend's startup drift assertion catches it only once a build
whose ORM *does* declare it tries to boot, which is exactly when the upgrade
fails.

This revision reconciles both objects by inspection rather than by revision
number, because the stamp cannot be trusted to say what is really there. Every
statement is guarded: on a database built correctly by the chain this migration
is a no-op.

The durable fix is the CI gate added alongside this revision, which provisions a
database the way the previous release did and then upgrades it — the customer
path, which CI never exercised.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_152"
down_revision = "v2_151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── v2_136: stack_instances.blueprint_release_id ─────────────────────────
    if inspector.has_table("stack_instances"):
        columns = {c["name"] for c in inspector.get_columns("stack_instances")}

        if "blueprint_release_id" not in columns:
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
                if_not_exists=True,
            )

        # v2_136 also relaxed template_id, so a blueprint-backed stack instance
        # can exist without a StackTemplate. A stamped-head install kept the
        # NOT NULL and rejects those rows at INSERT.
        template_id = next(
            (c for c in inspector.get_columns("stack_instances") if c["name"] == "template_id"),
            None,
        )
        if template_id is not None and not template_id["nullable"]:
            op.alter_column(
                "stack_instances",
                "template_id",
                existing_type=sa.Integer(),
                nullable=True,
            )

    # ── v2_138: container_registries ─────────────────────────────────────────
    # v2_138 is idempotent as of this change, so a stack upgrading through the
    # chain now gets the table either way. This covers the narrower case of a
    # database already stamped PAST v2_138 without the table — an install whose
    # upgrade aborted on the collision and was repaired by dropping it.
    if not inspector.has_table("container_registries"):
        op.create_table(
            "container_registries",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("type", sa.String(20), nullable=False),
            sa.Column("registry_host", sa.String(255), nullable=False),
            sa.Column("username", sa.String(255), nullable=True),
            sa.Column("token_encrypted", sa.Text(), nullable=True),
            sa.Column("far_service_account_encrypted", sa.Text(), nullable=True),
            sa.Column("credential_template_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("last_test_status", sa.String(32), nullable=True),
            sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_test_message", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["credential_template_id"], ["cloud_credential_templates.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
        op.create_index(
            "ix_container_registries_id", "container_registries", ["id"], if_not_exists=True
        )


    # ── redundant index on container_registries.name ─────────────────────────
    # The model declares `name = Column(..., unique=True, index=True)`, which
    # create_all renders as ONE unique index. v2_138 instead created a
    # UniqueConstraint AND a separate plain index, so a chain-built database
    # carries an extra non-unique index that create_all never builds. Harmless
    # in itself, but it is exactly the create_all-vs-chain divergence this
    # release is about, and it is reachable from here — so drop it rather than
    # record it in the parity allowlist. Guarded: absent on a create_all
    # database, present on a chain-built one.
    op.drop_index(
        "ix_container_registries_name",
        table_name="container_registries",
        if_exists=True,
    )


def downgrade() -> None:
    # Intentionally empty. This revision only ADDS objects that other revisions
    # already claim to own — v2_136 owns the column, v2_138 the table — and each
    # drops its own in its downgrade(). Dropping them here as well would make a
    # downgrade past this point destroy schema that a database reaching it by the
    # normal chain legitimately has.
    pass
