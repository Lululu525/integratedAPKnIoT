"""last update error on devices

Revision ID: 0008_device_last_error
Revises: 0007_firmware_active
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_device_last_error"
down_revision: str | None = "0007_firmware_active"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill, like the rest of the check-in columns: a
    # device that has never failed an update has nothing to say here, and one
    # that has overwrites the row on its next check-in anyway.
    op.add_column("devices", sa.Column("last_error", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("failed_attempts", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "failed_attempts")
    op.drop_column("devices", "last_error")
