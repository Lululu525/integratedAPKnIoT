"""firmware and devices belong to an account

Revision ID: 0012_owner_scoping
Revises: 0011_email_identity
Create Date: 2026-09-10
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_owner_scoping"
down_revision: str | None = "0011_email_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches `domain.models.DOWNLOAD_ID_BYTES`. Duplicated rather than imported: a
# migration describes the schema at one moment, and importing application code
# makes an old revision follow the current definition of a constant.
DOWNLOAD_ID_BYTES = 24


def upgrade() -> None:
    bind = op.get_bind()

    # Every existing row goes to the oldest account. Before this revision there
    # was one shared firmware list and one shared fleet, so the only reading
    # that loses nothing is that they all belonged to whoever was there first.
    # A database with no accounts has nothing to assign either way.
    first_owner = bind.execute(sa.text("SELECT MIN(id) FROM users")).scalar()

    op.add_column("firmware", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.add_column("firmware", sa.Column("download_id", sa.String(length=64), nullable=True))
    op.add_column("devices", sa.Column("owner_id", sa.Integer(), nullable=True))

    if first_owner is not None:
        bind.execute(sa.text("UPDATE firmware SET owner_id = :owner"), {"owner": first_owner})
        bind.execute(sa.text("UPDATE devices SET owner_id = :owner"), {"owner": first_owner})

    # One fresh identifier per row. Generated here rather than derived from
    # anything already on the row: a value computed from the id or the hash is
    # a value anyone holding those can compute too, which is the property this
    # column exists to not have.
    for (firmware_id,) in bind.execute(sa.text("SELECT id FROM firmware")).all():
        bind.execute(
            sa.text("UPDATE firmware SET download_id = :download_id WHERE id = :id"),
            {"download_id": secrets.token_urlsafe(DOWNLOAD_ID_BYTES), "id": firmware_id},
        )

    op.create_index("uq_firmware_download_id", "firmware", ["download_id"], unique=True)

    # Both uniqueness rules widen by one column. Without this, the first tenant
    # to publish `ESP32 1.0.0` stops every other tenant from ever publishing
    # their own, which is a shared namespace wearing multi-tenancy's clothes.
    op.drop_index("uq_firmware_model_version", table_name="firmware")
    op.drop_index("uq_firmware_model_sha256", table_name="firmware")
    op.create_index(
        "uq_firmware_owner_model_version",
        "firmware",
        ["owner_id", "model", "version"],
        unique=True,
    )
    op.create_index(
        "uq_firmware_owner_model_sha256",
        "firmware",
        ["owner_id", "model", "sha256"],
        unique=True,
    )
    op.create_index("ix_firmware_owner_id", "firmware", ["owner_id"])
    op.create_index("ix_devices_owner_id", "devices", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_devices_owner_id", table_name="devices")
    op.drop_index("ix_firmware_owner_id", table_name="firmware")
    op.drop_index("uq_firmware_owner_model_sha256", table_name="firmware")
    op.drop_index("uq_firmware_owner_model_version", table_name="firmware")
    op.create_index("uq_firmware_model_version", "firmware", ["model", "version"], unique=True)
    op.create_index("uq_firmware_model_sha256", "firmware", ["model", "sha256"], unique=True)
    op.drop_index("uq_firmware_download_id", table_name="firmware")
    op.drop_column("devices", "owner_id")
    op.drop_column("firmware", "download_id")
    op.drop_column("firmware", "owner_id")
