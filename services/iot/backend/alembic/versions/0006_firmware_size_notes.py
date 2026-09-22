"""size_bytes and notes on firmware

Revision ID: 0006_firmware_size_notes
Revises: 0005_device_checkin
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from config import get_settings

# revision identifiers, used by Alembic.
revision: str = "0006_firmware_size_notes"
down_revision: str | None = "0005_device_checkin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    firmware_dir = get_settings().firmware_dir
    rows = connection.execute(sa.text("SELECT id, filename FROM firmware")).all()

    # Refuse before the first ALTER, not partway through the backfill. Alembic
    # runs SQLite with non-transactional DDL, so a column added and then
    # abandoned stays on the table and the next attempt dies on "duplicate
    # column name", which names nothing the operator can act on.
    #
    # Naming every bad row at once matters for the same reason: these get
    # fixed by hand, and one per run is one round trip per row. Such a row is
    # already broken, serving a 404 on download that `main.ino` turns into a
    # reboot loop, so a stopped migration is the cheap way to hear about it.
    missing = [
        f"{row_id} ({filename})"
        for row_id, filename in rows
        if not (firmware_dir / filename).exists()
    ]
    if missing:
        raise RuntimeError(
            f"Cannot backfill size_bytes, no blob under {firmware_dir} for row "
            + ", ".join(missing)
        )

    op.add_column("firmware", sa.Column("size_bytes", sa.Integer(), nullable=True))
    op.add_column("firmware", sa.Column("notes", sa.String(), nullable=True))

    # Rows predate the column, so take each one's size off the blob it already
    # points at. A placeholder would be indistinguishable from a real size.
    for row_id, filename in rows:
        connection.execute(
            sa.text("UPDATE firmware SET size_bytes = :size WHERE id = :id"),
            {"size": (firmware_dir / filename).stat().st_size, "id": row_id},
        )

    with op.batch_alter_table("firmware") as batch_op:
        batch_op.alter_column("size_bytes", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.drop_column("firmware", "notes")
    op.drop_column("firmware", "size_bytes")
