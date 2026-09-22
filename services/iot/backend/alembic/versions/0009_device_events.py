"""append-only device event log

Revision ID: 0009_device_events
Revises: 0008_device_last_error
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_device_events"
down_revision: str | None = "0008_device_last_error"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No foreign key to `devices`: an event describes what a reported id did,
    # and must survive that row being replaced or removed.
    op.create_table(
        "device_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("from_version", sa.String(), nullable=True),
        sa.Column("to_version", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_device_events_device_id_id", "device_events", ["device_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_device_events_device_id_id", table_name="device_events")
    op.drop_table("device_events")
