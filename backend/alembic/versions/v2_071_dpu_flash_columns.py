"""Phase 4 — per-DPU flash state.

Tracks the lifecycle of a "Flash BFB, Firmware & config" operation so
the UI can show per-row status and cancel support without recomputing
from logs.

  flash_status          : idle | uploading | waiting_reboot | os_online | failed | cancelled
  flash_stage_detail    : free-form human progress string (e.g. "uploading bundle — 42%")
  flash_started_at      : timestamp of the most recent flash kickoff
  flash_completed_at    : timestamp of the most recent flash conclusion
  flash_error           : error message on failure (null otherwise)
  flash_celery_task_id  : Celery task id for cancellation

Revision ID: v2_071
Revises: v2_070
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_071"
down_revision = "v2_070"
branch_labels = None
depends_on = None


_COLS = [
    ("flash_status", sa.String(length=32)),
    ("flash_stage_detail", sa.String(length=255)),
    ("flash_started_at", sa.DateTime(timezone=True)),
    ("flash_completed_at", sa.DateTime(timezone=True)),
    ("flash_error", sa.Text()),
    ("flash_celery_task_id", sa.String(length=64)),
]


def upgrade() -> None:
    for name, col_type in _COLS:
        op.add_column("dpus", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column("dpus", name)
