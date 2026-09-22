"""each account holds the public key its uploads are verified against

Revision ID: 0014_account_public_key
Revises: 0013_device_registration
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_account_public_key"
down_revision: str | None = "0013_device_registration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, and nothing is backfilled with the server's old signing key.
    # That key signed for everybody, which is the property this revision
    # exists to remove: copying it onto every account would leave one leaked
    # key able to publish for all of them, spelled differently. An account
    # without a key cannot publish until it sets one.
    op.add_column("users", sa.Column("public_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "public_key")
