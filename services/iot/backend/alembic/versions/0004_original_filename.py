"""original_filename on firmware

Revision ID: 0004_original_filename
Revises: 0003_unique_firmware
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_original_filename"
down_revision: str | None = "0003_unique_firmware"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nothing is backfilled and no stored blob is renamed. There is no
    # production data, so rows written before this revision keep their
    # timestamp-based filenames and are still servable, but they are not
    # content-addressed. Re-upload to get a database where every row is.
    op.add_column("firmware", sa.Column("original_filename", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("firmware", "original_filename")
