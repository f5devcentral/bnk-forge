"""DPU rshim state — persist whether rshim is ready on the reachable side.

Discover (and the /rshim-status route) now probes rshim availability and
persists a short status string on the dpus row, so the UI can show a
per-DPU rshim readiness indicator without a secondary request.

Values stored in `rshim_state`:
  * "ready"       — rshim active + /dev/rshim0 present
  * "inactive"    — probe ran, rshim not ready for any reason
  * "unreachable" — SSH / network failure before we could probe
  (null = never checked)

`rshim_state_detail` holds the human-readable diagnostic from the probe
(or the exception text for unreachable); `rshim_checked_at` timestamps
the last attempt.

Revision ID: v2_081
Revises: v2_080
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_081"
down_revision = "v2_080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dpus", sa.Column("rshim_state", sa.String(length=32), nullable=True))
    op.add_column("dpus", sa.Column("rshim_state_detail", sa.Text(), nullable=True))
    op.add_column(
        "dpus",
        sa.Column("rshim_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dpus", "rshim_checked_at")
    op.drop_column("dpus", "rshim_state_detail")
    op.drop_column("dpus", "rshim_state")
