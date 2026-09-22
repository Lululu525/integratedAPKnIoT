"""active flag on firmware

Revision ID: 0007_firmware_active
Revises: 0006_firmware_size_notes
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_firmware_active"
down_revision: str | None = "0006_firmware_size_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL needs a non-null default on the existing rows, and that default
    # is the backfill: every row published so far is active.
    op.add_column(
        "firmware",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("firmware", "active")
