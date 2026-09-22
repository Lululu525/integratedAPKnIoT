"""Interfaces for reading and writing firmware and device records.

Spells out the database operations the rest of the backend relies on, without
committing to a particular database. `infrastructure/sqlite_repo.py` provides
the SQLite implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from domain.models import Device, DeviceEvent, EventType, Firmware, RefreshToken, User


class UserRepository(ABC):
    """Accounts. Wider than the rest of the backend needs, because fastapi-users
    reads through it: `update` and `delete` exist for the library's own routes
    (password reset writes a new hash, the users router deletes an account),
    not because anything in `application/` calls them.
    """

    @abstractmethod
    def add(self, user: User) -> User:
        """Persist a new user and return it with its assigned id.

        Raises `UserAlreadyExists` if the email is taken.
        """

    @abstractmethod
    def get_by_id(self, user_id: int) -> User | None:
        """Return a user by primary key, or None."""

    @abstractmethod
    def get_by_email(self, email: str) -> User | None:
        """Return a user by email, or None.

        The caller normalizes; see `domain.auth.normalize_email`. Doing it here
        instead would make the lookup disagree with the index the write side
        populated whenever one of the two forgot.
        """

    @abstractmethod
    def update(self, user: User) -> User:
        """Write a changed account back and return it."""

    @abstractmethod
    def delete(self, user: User) -> None:
        """Remove an account. Its refresh tokens go with it."""


class UserAlreadyExists(Exception):
    """Raised when registering an email that already exists."""


class UserNotFound(Exception):
    """Raised when an update targets an account id that has no row."""


class RefreshTokenRepository(ABC):
    """Storage for the opaque handles `POST /api/auth/refresh` rotates.

    There is no update: rotation issues a new row and deletes the old one, so a
    handle's value and the moment it was minted never change under it.
    """

    @abstractmethod
    def add(self, token: RefreshToken) -> RefreshToken:
        """Store a newly minted refresh token."""

    @abstractmethod
    def get(self, token: str) -> RefreshToken | None:
        """Return a stored token by its value, or None if it was never issued
        or has already been rotated away."""

    @abstractmethod
    def delete(self, token: str) -> None:
        """Drop one token. Silent when it is already gone, so a double refresh
        races to the same end state rather than to an error."""

    @abstractmethod
    def delete_for_user(self, user_id: int) -> None:
        """Drop every token an account holds, which is what logging out and
        changing a password both mean."""


class FirmwareAlreadyExists(Exception):
    """Raised when adding a firmware whose (model, version) is already stored."""


class FirmwareBinaryAlreadyExists(Exception):
    """Raised when the same binary is already stored for the model under another version.

    Carries the version it collided with, which is what the caller reports back.
    """

    def __init__(self, model: str, existing_version: str) -> None:
        super().__init__(model, existing_version)
        self.model = model
        self.existing_version = existing_version


class FirmwareNotFound(Exception):
    """Raised when a firmware operation targets an id that has no row."""


class FirmwareRepository(ABC):
    """Firmware, and who may see it.

    Every read that can return rows takes the account asking, and takes it as a
    required argument rather than reading it from anywhere. That is the point:
    a route added later cannot forget to filter, because there is nothing to
    call that does not ask. Putting the filter in the routes instead makes each
    new one another chance to hand out somebody else's builds with no test
    failing.

    `get_by_download_id` is the one exception and is unscoped on purpose. The
    device has no credential to send, so the identifier is itself the
    capability; see `Firmware.download_id`.
    """

    @abstractmethod
    def add(self, firmware: Firmware) -> Firmware:
        """Persist a new firmware row and return it with its assigned id.

        Both identity axes are enforced here rather than by the caller, since a
        caller can only read before it writes, and both are scoped to
        `firmware.owner_id`. Raises `FirmwareAlreadyExists` if that owner
        already has that (model, version), and `FirmwareBinaryAlreadyExists` if
        those bytes are already stored for that owner's model, the latter
        taking precedence when an insert collides on both.
        """

    @abstractmethod
    def get_by_id(self, firmware_id: int, owner_id: int) -> Firmware | None:
        """Return one of this owner's firmware rows, or None.

        None for an id belonging to someone else, exactly as for one that does
        not exist. The two are the same answer on purpose: telling them apart
        confirms which ids are real.
        """

    @abstractmethod
    def get_by_download_id(self, download_id: str) -> Firmware | None:
        """Return the firmware a download link addresses, or None. Unscoped."""

    @abstractmethod
    def get_by_sha256(self, model: str, sha256: str, owner_id: int) -> Firmware | None:
        """Return this owner's firmware storing exactly these contents, or None.

        Scoped per model as well as per owner, matching how uniqueness is
        scoped: one binary serving two models is unusual but not an error, and
        two tenants uploading identical bytes is neither.
        """

    @abstractmethod
    def get_latest_for_model(self, model: str, owner_id: int) -> Firmware | None:
        """Return the newest active firmware this owner has for a model, or None.

        Scoped like everything else, including for `POST /api/check`, which is
        unauthenticated: the device secret tells that route which account is
        asking before it gets here. Two tenants publishing under one model name
        is answerable only because of that.
        """

    @abstractmethod
    def deactivate(self, firmware_id: int, owner_id: int) -> Firmware | None:
        """Clear `active` on one of this owner's rows and return it, or None.

        Idempotent: deactivating an already-inactive row changes nothing and
        still returns the row.
        """

    @abstractmethod
    def list_all(self, owner_id: int) -> list[Firmware]:
        """Return this owner's firmware, newest first."""


class DeviceRepository(ABC):
    """Devices, and who may see them.

    A row exists because someone registered it. Nothing here creates one from a
    check-in, which is the difference registration makes: before it, posting
    any string to `/api/check` conjured a device record out of nothing.
    """

    @abstractmethod
    def register(self, device: Device) -> Device:
        """Store a newly registered device and return it with its assigned id.

        Raises `DeviceAlreadyExists` if that device id is taken. The caller
        generates the id, so a collision means two calls drew the same random
        value, not that anyone chose it.
        """

    @abstractmethod
    def get_by_device_id(self, device_id: str) -> Device | None:
        """Return a device by its identifier, or None. Unscoped.

        `POST /api/check` is unauthenticated, and this is how it finds out
        whose device is calling, so it cannot itself be scoped to an owner. The
        caller still has to match the secret before treating the answer as the
        device speaking.
        """

    @abstractmethod
    def record_checkin(self, device: Device) -> Device | None:
        """Write what a device reported about itself, or None if it has no row.

        Never inserts, and never changes the owner, the secret or the enabled
        flag: a check-in is the device describing itself, and none of those are
        the device's to say.
        """

    @abstractmethod
    def set_enabled(self, device_id: str, owner_id: int, enabled: bool) -> Device | None:
        """Switch one of this owner's devices on or off, or None if unknown."""

    @abstractmethod
    def list_all(self, owner_id: int) -> list[Device]:
        """Return this owner's devices, most recently seen first."""


class DeviceAlreadyExists(Exception):
    """Raised when registering a device id that is already stored."""


class DeviceEventRepository(ABC):
    """The OTA history. Append and read; there is no update and no delete."""

    @abstractmethod
    def add(self, event: DeviceEvent) -> DeviceEvent:
        """Append one event."""

    @abstractmethod
    def list_for_device(self, device_id: str, limit: int = 100) -> list[DeviceEvent]:
        """Return one device's events, newest first."""

    @abstractmethod
    def latest_for_device(self, device_id: str, event_type: EventType) -> DeviceEvent | None:
        """Return the most recent event of one type for a device, or None."""
