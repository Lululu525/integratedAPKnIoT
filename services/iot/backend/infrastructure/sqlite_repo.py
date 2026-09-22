"""SQLite-backed reads and writes for firmware and device records.

Implements the interfaces in `ports/repository.py` using SQLAlchemy, and maps
each table row to and from the domain dataclasses.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain.models import Device, DeviceEvent, EventType, Firmware, RefreshToken, User
from domain.signing import parse_version
from ports.repository import (
    DeviceAlreadyExists,
    DeviceEventRepository,
    DeviceRepository,
    FirmwareAlreadyExists,
    FirmwareBinaryAlreadyExists,
    FirmwareRepository,
    RefreshTokenRepository,
    UserAlreadyExists,
    UserNotFound,
    UserRepository,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from infrastructure.db import (
    DeviceEventRow,
    DeviceRow,
    FirmwareRow,
    RefreshTokenRow,
    UserRow,
)


def _utc(value: datetime | None) -> datetime | None:
    """Re-attach the UTC offset SQLite drops.

    Every timestamp is written as UTC (`db._utcnow`), but SQLite has no
    timezone type and hands the value back naive. Anything serializing a naive
    datetime produces an ISO string with no offset, which a browser reads as
    local time. Stamping here is what keeps that from being each caller's
    problem to remember.
    """
    return value.replace(tzinfo=timezone.utc) if value is not None else None


def _to_firmware(row: FirmwareRow) -> Firmware:
    return Firmware(
        id=row.id,
        owner_id=row.owner_id,
        download_id=row.download_id,
        model=row.model,
        version=row.version,
        filename=row.filename,
        original_filename=row.original_filename,
        signature=row.signature,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        notes=row.notes,
        active=row.active,
        created_at=_utc(row.created_at),
    )


def _to_event(row: DeviceEventRow) -> DeviceEvent:
    return DeviceEvent(
        id=row.id,
        device_id=row.device_id,
        event_type=EventType(row.event_type),
        from_version=row.from_version,
        to_version=row.to_version,
        created_at=_utc(row.created_at),
    )


def _to_device(row: DeviceRow) -> Device:
    return Device(
        id=row.id,
        owner_id=row.owner_id,
        secret_hash=row.secret_hash,
        enabled=row.enabled,
        registered_at=_utc(row.registered_at),
        device_id=row.device_id,
        model=row.model,
        current_version=row.current_version,
        last_seen=_utc(row.last_seen),
        poll_interval_seconds=row.poll_interval_seconds,
        rssi=row.rssi,
        ip=row.ip,
        last_error=row.last_error,
        failed_attempts=row.failed_attempts,
    )


def _to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        hashed_password=row.hashed_password,
        is_active=row.is_active,
        is_superuser=row.is_superuser,
        is_verified=row.is_verified,
        public_key=row.public_key,
        created_at=_utc(row.created_at),
    )


def _to_refresh_token(row: RefreshTokenRow) -> RefreshToken:
    return RefreshToken(
        token=row.token,
        user_id=row.user_id,
        created_at=_utc(row.created_at),
    )


class SqliteUserRepository(UserRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, user: User) -> User:
        row = UserRow(
            email=user.email,
            hashed_password=user.hashed_password,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            is_verified=user.is_verified,
            public_key=user.public_key,
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise UserAlreadyExists(user.email) from exc
        self._session.refresh(row)
        return _to_user(row)

    def get_by_id(self, user_id: int) -> User | None:
        row = self._session.get(UserRow, user_id)
        return _to_user(row) if row else None

    def get_by_email(self, email: str) -> User | None:
        row = self._session.scalar(select(UserRow).where(UserRow.email == email))
        return _to_user(row) if row else None

    def update(self, user: User) -> User:
        row = self._session.get(UserRow, user.id)
        if row is None:
            raise UserNotFound(user.id)
        row.email = user.email
        row.hashed_password = user.hashed_password
        row.is_active = user.is_active
        row.is_superuser = user.is_superuser
        row.is_verified = user.is_verified
        row.public_key = user.public_key
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise UserAlreadyExists(user.email) from exc
        self._session.refresh(row)
        return _to_user(row)

    def delete(self, user: User) -> None:
        # The tokens go first and explicitly. The foreign key declares a
        # cascade, but SQLite ignores foreign keys unless the connection turns
        # them on, so relying on it would leave handles behind that still name
        # a user id, and the next account to be handed that id would inherit
        # live sessions belonging to nobody.
        self._session.execute(delete(RefreshTokenRow).where(RefreshTokenRow.user_id == user.id))
        self._session.execute(delete(UserRow).where(UserRow.id == user.id))
        self._session.commit()


class SqliteRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, token: RefreshToken) -> RefreshToken:
        row = RefreshTokenRow(token=token.token, user_id=token.user_id)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _to_refresh_token(row)

    def get(self, token: str) -> RefreshToken | None:
        row = self._session.get(RefreshTokenRow, token)
        return _to_refresh_token(row) if row else None

    def delete(self, token: str) -> None:
        self._session.execute(delete(RefreshTokenRow).where(RefreshTokenRow.token == token))
        self._session.commit()

    def delete_for_user(self, user_id: int) -> None:
        self._session.execute(delete(RefreshTokenRow).where(RefreshTokenRow.user_id == user_id))
        self._session.commit()


class SqliteFirmwareRepository(FirmwareRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, firmware: Firmware) -> Firmware:
        row = FirmwareRow(
            owner_id=firmware.owner_id,
            download_id=firmware.download_id,
            model=firmware.model,
            version=firmware.version,
            filename=firmware.filename,
            original_filename=firmware.original_filename,
            signature=firmware.signature,
            sha256=firmware.sha256,
            size_bytes=firmware.size_bytes,
            notes=firmware.notes,
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            conflict = self._conflict(firmware)
            if conflict is None:
                raise
            raise conflict from exc
        self._session.refresh(row)
        return _to_firmware(row)

    def _conflict(self, firmware: Firmware) -> Exception | None:
        """Name which uniqueness a rejected insert violated, or None if neither.

        Decided by re-reading rather than by parsing the driver's message, which
        names columns on SQLite and constraint names elsewhere. The sha256 axis is
        checked first so that one input gets one answer: an upload of bytes that
        are already stored is reported the same way whether the use case's
        pre-check caught it or the index did.

        None means the row was rejected for something this does not know about,
        and the caller re-raises the original error rather than mislabelling it.
        """
        duplicate = self.get_by_sha256(firmware.model, firmware.sha256, firmware.owner_id)
        if duplicate is not None:
            return FirmwareBinaryAlreadyExists(firmware.model, duplicate.version)
        existing = self._session.scalar(
            select(FirmwareRow).where(
                FirmwareRow.owner_id == firmware.owner_id,
                FirmwareRow.model == firmware.model,
                FirmwareRow.version == firmware.version,
            )
        )
        if existing is not None:
            return FirmwareAlreadyExists(firmware.model, firmware.version)
        return None

    def get_by_id(self, firmware_id: int, owner_id: int) -> Firmware | None:
        row = self._session.scalar(
            select(FirmwareRow).where(
                FirmwareRow.id == firmware_id, FirmwareRow.owner_id == owner_id
            )
        )
        return _to_firmware(row) if row else None

    def get_by_download_id(self, download_id: str) -> Firmware | None:
        row = self._session.scalar(
            select(FirmwareRow).where(FirmwareRow.download_id == download_id)
        )
        return _to_firmware(row) if row else None

    def get_by_sha256(self, model: str, sha256: str, owner_id: int) -> Firmware | None:
        row = self._session.scalar(
            select(FirmwareRow).where(
                FirmwareRow.owner_id == owner_id,
                FirmwareRow.model == model,
                FirmwareRow.sha256 == sha256,
            )
        )
        return _to_firmware(row) if row else None

    def get_latest_for_model(self, model: str, owner_id: int) -> Firmware | None:
        rows = self._session.scalars(
            select(FirmwareRow).where(
                FirmwareRow.owner_id == owner_id,
                FirmwareRow.model == model,
                FirmwareRow.active,
            )
        ).all()
        if not rows:
            return None
        latest = max(rows, key=lambda r: (parse_version(r.version), r.id))
        return _to_firmware(latest)

    def deactivate(self, firmware_id: int, owner_id: int) -> Firmware | None:
        row = self._session.scalar(
            select(FirmwareRow).where(
                FirmwareRow.id == firmware_id, FirmwareRow.owner_id == owner_id
            )
        )
        if row is None:
            return None
        # Set unconditionally rather than branching on the current value, so a
        # second call still succeeds with the row unchanged.
        row.active = False
        self._session.commit()
        self._session.refresh(row)
        return _to_firmware(row)

    def list_all(self, owner_id: int) -> list[Firmware]:
        rows = self._session.scalars(
            select(FirmwareRow)
            .where(FirmwareRow.owner_id == owner_id)
            .order_by(FirmwareRow.created_at.desc(), FirmwareRow.id.desc())
        ).all()
        return [_to_firmware(r) for r in rows]


class SqliteDeviceRepository(DeviceRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def register(self, device: Device) -> Device:
        row = DeviceRow(
            device_id=device.device_id,
            owner_id=device.owner_id,
            model=device.model,
            secret_hash=device.secret_hash,
            enabled=device.enabled,
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DeviceAlreadyExists(device.device_id) from exc
        self._session.refresh(row)
        return _to_device(row)

    def get_by_device_id(self, device_id: str) -> Device | None:
        row = self._session.scalar(select(DeviceRow).where(DeviceRow.device_id == device_id))
        return _to_device(row) if row else None

    def record_checkin(self, device: Device) -> Device | None:
        row = self._session.scalar(select(DeviceRow).where(DeviceRow.device_id == device.device_id))
        if row is None:
            return None

        # Only what the device reported. `owner_id`, `secret_hash` and
        # `enabled` are absent on purpose: a check-in that could write them
        # would let a unit hand itself to another account, or switch itself
        # back on after being disabled.
        row.model = device.model
        row.current_version = device.current_version
        row.last_seen = device.last_seen
        row.poll_interval_seconds = device.poll_interval_seconds
        row.rssi = device.rssi
        row.ip = device.ip
        # Assigned unconditionally like every other column: the device resends
        # what is still true, so a cleared error has to clear the column.
        row.last_error = device.last_error
        row.failed_attempts = device.failed_attempts
        self._session.commit()
        self._session.refresh(row)
        return _to_device(row)

    def set_enabled(self, device_id: str, owner_id: int, enabled: bool) -> Device | None:
        row = self._session.scalar(
            select(DeviceRow).where(
                DeviceRow.device_id == device_id, DeviceRow.owner_id == owner_id
            )
        )
        if row is None:
            return None
        row.enabled = enabled
        self._session.commit()
        self._session.refresh(row)
        return _to_device(row)

    def list_all(self, owner_id: int) -> list[Device]:
        # SQLite sorts NULL as smallest, so never-seen devices land last on desc.
        rows = self._session.scalars(
            select(DeviceRow)
            .where(DeviceRow.owner_id == owner_id)
            .order_by(DeviceRow.last_seen.desc(), DeviceRow.id.desc())
        ).all()
        return [_to_device(r) for r in rows]


class SqliteDeviceEventRepository(DeviceEventRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: DeviceEvent) -> DeviceEvent:
        row = DeviceEventRow(
            device_id=event.device_id,
            event_type=event.event_type.value,
            from_version=event.from_version,
            to_version=event.to_version,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _to_event(row)

    def list_for_device(self, device_id: str, limit: int = 100) -> list[DeviceEvent]:
        # Ordered by id, not `created_at`: two events from one check-in share a
        # timestamp to the resolution SQLite stores, and insertion order is the
        # only thing that puts them back in the order they happened.
        rows = self._session.scalars(
            select(DeviceEventRow)
            .where(DeviceEventRow.device_id == device_id)
            .order_by(DeviceEventRow.id.desc())
            .limit(limit)
        ).all()
        return [_to_event(r) for r in rows]

    def latest_for_device(self, device_id: str, event_type: EventType) -> DeviceEvent | None:
        row = self._session.scalar(
            select(DeviceEventRow)
            .where(
                DeviceEventRow.device_id == device_id,
                DeviceEventRow.event_type == event_type.value,
            )
            .order_by(DeviceEventRow.id.desc())
            .limit(1)
        )
        return _to_event(row) if row else None
