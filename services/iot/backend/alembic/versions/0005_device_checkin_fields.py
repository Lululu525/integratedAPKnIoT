"""poll interval, rssi and ip on devices

Revision ID: 0005_device_checkin
Revises: 0004_original_filename
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_device_checkin"
down_revision: str | None = "0004_original_filename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill: these are readings, not facts about the row,
    # and a device rewrites all three on its next check-in. A device still
    # running firmware from before this revision reports none of them and
    # `domain.fleet.is_online` answers None for it rather than guessing.
    op.add_column("devices", sa.Column("poll_interval_seconds", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("rssi", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("ip", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "ip")
    op.drop_column("devices", "rssi")
    op.drop_column("devices", "poll_interval_seconds")
