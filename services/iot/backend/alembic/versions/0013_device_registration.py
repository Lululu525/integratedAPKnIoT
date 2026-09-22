"""devices must be registered before they can check in

Revision ID: 0013_device_registration
Revises: 0012_owner_scoping
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_device_registration"
down_revision: str | None = "0012_owner_scoping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No secret is invented for the rows already here, and they stop being able
    # to check in the moment this runs. That is the point of the revision: a
    # row that appeared because something posted a device id is exactly what
    # registration removes. A secret has to reach the unit's LittleFS image to
    # be of any use, so minting one here would only make an unusable value look
    # like a working registration. Register each unit again and reflash it.
    op.add_column("devices", sa.Column("secret_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "devices",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("devices", sa.Column("registered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "registered_at")
    op.drop_column("devices", "enabled")
    op.drop_column("devices", "secret_hash")
