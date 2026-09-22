"""unique (model, sha256) on firmware

Revision ID: 0010_unique_binary
Revises: 0009_device_events
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_unique_binary"
down_revision: str | None = "0009_device_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Refuse rather than delete, unlike 0003. A (model, version) collision left
    # rows that could not be served at all, so dropping one lost nothing. Two
    # versions sharing one binary are both servable, and choosing which one
    # disappears is not a migration's call. There is no production data, so
    # this names what it found and stops.
    duplicates = op.get_bind().execute(sa.text("""
        SELECT model, sha256, GROUP_CONCAT(version) AS versions
        FROM firmware
        GROUP BY model, sha256
        HAVING COUNT(*) > 1
        """)).all()
    if duplicates:
        listed = "; ".join(
            f"{model} {sha[:12]} as {versions}" for model, sha, versions in duplicates
        )
        raise RuntimeError(
            f"firmware rows share one binary across versions: {listed}. "
            "Delete the versions that should not be served, then re-run this migration."
        )

    op.create_index("uq_firmware_model_sha256", "firmware", ["model", "sha256"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_firmware_model_sha256", table_name="firmware")
