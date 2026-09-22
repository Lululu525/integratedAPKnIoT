"""Shared test setup and the in-memory doubles for every port.

`infrastructure.db` creates its engine at import time from `get_settings()`,
which defaults to `backend/data/`. Point `DATA_DIR` at a throwaway directory
before any test module can trigger that import, so running the suite never
touches real application data. `JWT_SECRET` has no default in config on purpose,
so seed one here for the same reason, before config is ever imported.

The fakes below subclass the abstract ports, which is what makes them useful:
a method added to a port turns every fake missing it into a TypeError at
construction. Duplicated per-module doubles cannot do that, and drifted.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ota-test-data-"))
# 32+ bytes: config enforces RFC 7518's minimum HMAC key length for HS256.
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-padded-to-length")


# Throwaway, because the setup scripts write there and a test that runs one
# must not land in the developer's own keys. The server itself reads nothing
# from it: firmware is verified against the public key on the uploading account.
os.environ.setdefault("KEYS_DIR", tempfile.mkdtemp(prefix="ota-test-keys-"))


# Imported after the environment is seeded: config must not be read before the
# lines above have run.
from collections.abc import Iterable, Iterator  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from domain.models import (  # noqa: E402
    Device,
    DeviceEvent,
    EventType,
    Firmware,
    RefreshToken,
    User,
)
from domain.signing import parse_version  # noqa: E402
from ports.repository import (  # noqa: E402
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
from ports.storage import CHUNK_SIZE, StorageBackend  # noqa: E402


class FakeFirmwareRepository(FirmwareRepository):
    """Rows in a list, queried the way the real repository queries them.

    Seeding rows rather than one lookup table per method means the answers
    cannot contradict each other, and a row written through `add` is visible to
    every read afterwards.

    Owner scoping is enforced here too. Subclassing an abstract port catches a
    method that was added to it, never behaviour, so a fake that answered
    across owners would leave every caller-side test green on the one property
    the port exists to guarantee.
    """

    def __init__(self, rows: Iterable[Firmware] = ()) -> None:
        self.rows: list[Firmware] = list(rows)
        self.added: list[Firmware] = []

    def add(self, firmware: Firmware) -> Firmware:
        # Both rejections the port promises, scoped to the owner the way the
        # indexes are: two accounts may each hold their own copy of a version.
        duplicate = self.get_by_sha256(firmware.model, firmware.sha256, firmware.owner_id)
        if duplicate is not None:
            raise FirmwareBinaryAlreadyExists(firmware.model, duplicate.version)
        if any(
            f.owner_id == firmware.owner_id
            and f.model == firmware.model
            and f.version == firmware.version
            for f in self.rows
        ):
            raise FirmwareAlreadyExists(firmware.model, firmware.version)
        firmware.id = len(self.rows) + 1
        self.rows.append(firmware)
        self.added.append(firmware)
        return firmware

    def get_by_id(self, firmware_id: int, owner_id: int) -> Firmware | None:
        return next((f for f in self.rows if f.id == firmware_id and f.owner_id == owner_id), None)

    def get_by_download_id(self, download_id: str) -> Firmware | None:
        return next((f for f in self.rows if f.download_id == download_id), None)

    def get_by_sha256(self, model: str, sha256: str, owner_id: int) -> Firmware | None:
        return next(
            (
                f
                for f in self.rows
                if f.owner_id == owner_id and f.model == model and f.sha256 == sha256
            ),
            None,
        )

    def get_latest_for_model(self, model: str, owner_id: int) -> Firmware | None:
        candidates = [
            f for f in self.rows if f.model == model and f.active and f.owner_id == owner_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda f: (parse_version(f.version), f.id or 0))

    def deactivate(self, firmware_id: int, owner_id: int) -> Firmware | None:
        firmware = self.get_by_id(firmware_id, owner_id)
        if firmware is None:
            return None
        firmware.active = False
        return firmware

    def list_all(self, owner_id: int) -> list[Firmware]:
        # Insertion order. Ordering is SQL's job and is covered against the real
        # repository, so imitating it here would only be a second claim about it
        # that nothing checks.
        return [f for f in self.rows if f.owner_id == owner_id]


class FakeDeviceRepository(DeviceRepository):
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self._next_id = 1

    def register(self, device: Device) -> Device:
        if device.device_id in self.devices:
            raise DeviceAlreadyExists(device.device_id)
        device.id = self._next_id
        self._next_id += 1
        device.registered_at = device.registered_at or datetime.now(timezone.utc)
        self.devices[device.device_id] = device
        return device

    def get_by_device_id(self, device_id: str) -> Device | None:
        return self.devices.get(device_id)

    def record_checkin(self, device: Device) -> Device | None:
        # Never inserts, and carries the three columns a check-in may not
        # write over from the stored row, matching the real repository.
        existing = self.devices.get(device.device_id)
        if existing is None:
            return None
        device.id = existing.id
        device.owner_id = existing.owner_id
        device.secret_hash = existing.secret_hash
        device.enabled = existing.enabled
        device.registered_at = existing.registered_at
        self.devices[device.device_id] = device
        return device

    def set_enabled(self, device_id: str, owner_id: int, enabled: bool) -> Device | None:
        device = self.devices.get(device_id)
        if device is None or device.owner_id != owner_id:
            return None
        device.enabled = enabled
        return device

    def list_all(self, owner_id: int) -> list[Device]:
        return [d for d in self.devices.values() if d.owner_id == owner_id]


class FakeDeviceEventRepository(DeviceEventRepository):
    def __init__(self) -> None:
        self.events: list[DeviceEvent] = []

    def add(self, event: DeviceEvent) -> DeviceEvent:
        event.id = len(self.events) + 1
        self.events.append(event)
        return event

    def list_for_device(self, device_id: str, limit: int = 100) -> list[DeviceEvent]:
        matching = [e for e in self.events if e.device_id == device_id]
        return list(reversed(matching))[:limit]

    def latest_for_device(self, device_id: str, event_type: EventType) -> DeviceEvent | None:
        matching = [
            e for e in self.events if e.device_id == device_id and e.event_type == event_type
        ]
        return matching[-1] if matching else None

    def types(self) -> list[EventType]:
        """Every event type recorded, in order. What most tests actually assert."""
        return [e.event_type for e in self.events]


class FakeUserRepository(UserRepository):
    """Keyed on email, and only ever on the normalized form.

    The real repository gets that guarantee from a unique index over a column
    the adapter lowercases on the way in. A fake keyed on whatever it was
    handed would accept two spellings of one address and let a test pass that
    the database would reject, so it asserts the invariant instead.
    """

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self._next_id = 1

    def add(self, user: User) -> User:
        assert user.email == user.email.strip().lower(), "email must be normalized before add"
        if user.email in self.users:
            raise UserAlreadyExists(user.email)
        user.id = self._next_id
        self._next_id += 1
        self.users[user.email] = user
        return user

    def get_by_id(self, user_id: int) -> User | None:
        return next((u for u in self.users.values() if u.id == user_id), None)

    def get_by_email(self, email: str) -> User | None:
        return self.users.get(email)

    def update(self, user: User) -> User:
        existing = self.get_by_id(user.id) if user.id is not None else None
        if existing is None:
            raise UserNotFound(user.id)
        stored_under = next(k for k, v in self.users.items() if v.id == user.id)
        if stored_under != user.email and user.email in self.users:
            raise UserAlreadyExists(user.email)
        del self.users[stored_under]
        self.users[user.email] = user
        return user

    def delete(self, user: User) -> None:
        self.users = {k: v for k, v in self.users.items() if v.id != user.id}


class FakeRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self) -> None:
        self.tokens: dict[str, RefreshToken] = {}

    def add(self, token: RefreshToken) -> RefreshToken:
        if token.created_at is None:
            token.created_at = datetime.now(timezone.utc)
        self.tokens[token.token] = token
        return token

    def get(self, token: str) -> RefreshToken | None:
        return self.tokens.get(token)

    def delete(self, token: str) -> None:
        self.tokens.pop(token, None)

    def delete_for_user(self, user_id: int) -> None:
        self.tokens = {k: v for k, v in self.tokens.items() if v.user_id != user_id}


class FakeStorage(StorageBackend):
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = files or {}

    def put(self, filename: str, data: bytes) -> None:
        self.files[filename] = data

    def get(self, filename: str) -> bytes:
        return self.files[filename]

    def iter_chunks(self, filename: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        # Chunked for real, so a test can tell a streamed response from one
        # that yields the whole file in a single piece.
        data = self.files[filename]
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    def delete(self, filename: str) -> None:
        self.files.pop(filename, None)

    def exists(self, filename: str) -> bool:
        return filename in self.files
