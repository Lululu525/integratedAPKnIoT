"""initial schema: firmware and devices

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "firmware",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("signature", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_firmware_model", "firmware", ["model"])

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("current_version", sa.String(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_devices_device_id", "devices", ["device_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_devices_device_id", table_name="devices")
    op.drop_table("devices")
    op.drop_index("ix_firmware_model", table_name="firmware")
    op.drop_table("firmware")
