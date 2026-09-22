"""unique (model, version) on firmware

Revision ID: 0003_unique_firmware
Revises: 0002_users
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_unique_firmware"
down_revision: str | None = "0002_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep the highest id per (model, version), matching get_latest_for_model's tie-break.
    op.execute("""
        DELETE FROM firmware
        WHERE id NOT IN (SELECT MAX(id) FROM firmware GROUP BY model, version)
        """)
    op.create_index("uq_firmware_model_version", "firmware", ["model", "version"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_firmware_model_version", table_name="firmware")
