"""SQLAlchemy setup and the database table definitions.

Builds the engine and session factory, and declares the `firmware`,
`devices` and `device_events` tables. `sqlite_repo.py` converts between these
table rows and the domain dataclasses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from config import get_settings
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FirmwareRow(Base):
    __tablename__ = "firmware"

    # Both identity axes are enforced here, not just in the use case: the
    # sha256 check there reads before it writes, so two concurrent uploads of
    # one binary both see nothing and both proceed. Both are scoped to the
    # owner, so one tenant publishing a version never blocks another from
    # publishing their own under the same name.
    __table_args__ = (
        Index("uq_firmware_owner_model_version", "owner_id", "model", "version", unique=True),
        Index("uq_firmware_owner_model_sha256", "owner_id", "model", "sha256", unique=True),
        Index("uq_firmware_download_id", "download_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Nullable only because rows predating 0012 on a database with no accounts
    # have nobody to belong to. Everything written since carries one.
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    # The public handle on the binary, and the only thing guarding it: the
    # download route is unauthenticated because `ota.cpp` has no credential to
    # send. Unique so it addresses exactly one row, random so holding one link
    # says nothing about any other.
    download_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: rows predating migration 0004 have no name to show.
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    # Mapper default: `sqlite_repo.add` does not pass `active`, and a freshly
    # uploaded firmware is always live.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signature: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class UserRow(Base):
    """A dashboard account, columns named the way fastapi-users names them.

    `is_superuser` is the only authorization state stored. There is no `role`
    column: `domain.models.User.role` reads this flag, so the two can never
    disagree.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 320 is the longest address RFC 5321 permits, and the width the library's
    # own table uses. Stored lowercased, so the unique index is what actually
    # stops one inbox holding two accounts; see `domain.auth.normalize_email`.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    # Wide enough for any pwdlib output. The hash names its own algorithm, so
    # argon2 and bcrypt rows sit in this column together with nothing to record.
    hashed_password: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The key this account's uploads are verified against, PEM. Null until
    # the account sets one, and an account without one cannot publish: the
    # server holds no private key of its own to fall back to.
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class RefreshTokenRow(Base):
    """One live refresh handle. Rotation deletes the old row and inserts a new one.

    The token value is the primary key rather than a surrogate id, because it
    is the only thing anything ever looks a row up by, and a second identifier
    would be a way to reach a handle without holding it.

    SQLite does not enforce foreign keys unless the connection asks it to, and
    this one does not, so the reference below documents the relationship while
    `SqliteUserRepository.delete` is what actually clears an account's tokens.
    """

    __tablename__ = "refresh_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class DeviceRow(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    # Null until the unit is registered to an account, which is what a check-in
    # from an unknown device still produces. Such a row is on nobody's
    # dashboard: `list_all` is scoped and there is no owner to match.
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    model: Mapped[str] = mapped_column(String, nullable=False)
    current_version: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    failed_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # SHA-256 of the secret, hex. A plain digest and not a password hash: the
    # secret is 32 bytes of urandom, so there is no dictionary to run against
    # it, and this is verified on every check-in, which for a fleet polling
    # every six seconds is the one place a deliberately slow hash would hurt.
    secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Withdrawing one unit without touching the rest. Checked on every check-in
    # rather than at registration, so disabling takes effect on the device's
    # next poll instead of whenever its token would have expired.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Mapper default rather than a column one, matching `created_at`
    # elsewhere. Nullable because rows predating registration have no such
    # moment, and nothing invents one for them.
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow, nullable=True)


class DeviceEventRow(Base):
    """Append-only OTA history. Nothing updates or deletes a row here.

    A check-in that carries no news is not recorded. Devices poll every few
    seconds, so writing a row per check-in would add tens of thousands per
    device per day, all saying what `devices.last_seen` already says. A `check`
    row is written only when the server had an update to offer; a check-in on a
    version that changed writes `success` or `rollback` instead.

    `device_id` is the reported string rather than a foreign key to `devices`,
    so an event outlives the row it describes and a download that cannot be
    attributed still records.
    """

    __tablename__ = "device_events"

    # Reads are always "this device's history, newest first".
    __table_args__ = (Index("ix_device_events_device_id_id", "device_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    from_version: Mapped[str | None] = mapped_column(String, nullable=True)
    to_version: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


def make_engine():
    url = make_url(get_settings().database_url)
    # Derived from the URL rather than from `db_path`, so setting DATABASE_URL
    # cannot leave the engine opening one file while the directory of another
    # gets created. SQLite is the only backend with a directory to create, and
    # it will not create one itself; `sqlite://` alone means in-memory.
    if url.drivername.startswith("sqlite") and url.database:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
