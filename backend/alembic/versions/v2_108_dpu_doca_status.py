"""Add doca_status JSON column to dpus table."""
import sqlalchemy as sa

from alembic import op

revision = "v2_108"
down_revision = "v2_107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dpus", sa.Column("doca_status", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("dpus", "doca_status")
