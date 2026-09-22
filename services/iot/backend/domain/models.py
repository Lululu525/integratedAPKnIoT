"""Data structures for the firmware and devices this server tracks.

`Firmware` is one uploaded build: which model and version it is for, the stored
file, and its hash and signature. `Device` is one ESP32 unit and the version it
last reported. Plain dataclasses, passed around by the rest of the backend.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """What happened to a device, as recorded in the event log.

    `check` is a check-in worth keeping, `download` is a binary handed out,
    and `success` and `rollback` are read off a device's version changing
    between two check-ins. Stored as the string value.
    """

    CHECK = "check"
    DOWNLOAD = "download"
    SUCCESS = "success"
    ROLLBACK = "rollback"


@dataclass
class User:
    """A dashboard account, shaped to fastapi-users' `UserProtocol`.

    The field names are the library's, not ours: `email`, `hashed_password`,
    `is_active`, `is_superuser` and `is_verified` are read by name off whatever
    object the database adapter returns, so a dataclass that renames any of
    them stops being a user as far as the routers are concerned.

    `hashed_password` is whatever pwdlib produced. The hash names its own
    algorithm, so nothing needs to record which.

    `is_verified` and `is_superuser` are stored and never checked; the protocol
    requires both. Verification needs a mail transport this server does not
    have, and there is no privileged account: an account reaches what it owns,
    which is the whole of the authorization model.

    `public_key` is the key this account's uploads are verified against. The
    matching private key never reaches the server, which is the point: a
    compromised server cannot produce firmware any device will accept. An
    account that has not set one cannot publish.
    """

    email: str
    hashed_password: str
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False
    public_key: str | None = None
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class RefreshToken:
    """One long-lived, opaque handle that mints fresh access tokens.

    Opaque and stored rather than a second JWT, because the point of it is to
    be revocable: a stateless refresh token cannot be withdrawn before it
    expires, which is the only thing it would buy over a longer access token.
    Rotated on every use, so a stolen handle is usable at most once before the
    real client's next refresh invalidates it.
    """

    token: str
    user_id: int
    created_at: datetime | None = None


# 24 bytes of urandom, base64url encoded to 32 characters. The download route
# cannot authenticate, so this is the whole credential; sized so that guessing
# one is not a thing anyone attempts.
DOWNLOAD_ID_BYTES = 24


def new_download_id() -> str:
    return secrets.token_urlsafe(DOWNLOAD_ID_BYTES)


# The identifier a registered unit reports. Minted here rather than derived
# from the MAC address, which is not secret and within one vendor prefix is
# enumerable, so it would name unregistered devices as readily as registered
# ones.
DEVICE_ID_BYTES = 12

# The secret that proves a check-in came from that unit. Long enough that
# guessing is not a strategy, which is what lets the server store a plain
# SHA-256 of it rather than a deliberately slow password hash.
DEVICE_SECRET_BYTES = 32


def new_device_id() -> str:
    return secrets.token_urlsafe(DEVICE_ID_BYTES)


def new_device_secret() -> str:
    return secrets.token_urlsafe(DEVICE_SECRET_BYTES)


def hash_device_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secret_matches(secret: str, secret_hash: str | None) -> bool:
    """Whether a presented secret is the one this device registered with.

    Compared in constant time. The digests are public-length and the comparison
    is not the weak point here, but a byte-at-a-time `==` on a value an
    attacker supplies is the kind of thing that is only ever noticed later.
    """
    if not secret_hash:
        return False
    return hmac.compare_digest(hash_device_secret(secret), secret_hash)


@dataclass
class Firmware:
    """A single firmware build for a given device model.

    `signature` is the base64-encoded RSA-PSS signature over the manifest
    `model|version|sha256` and must stay byte-for-byte compatible with what
    the ESP32 verifies on-device.

    `filename` is the storage key, `{sha256}.bin`, and carries no meaning
    beyond addressing the bytes. Because it is derived from the contents, one
    blob can back several rows, including rows for different models. Anything
    that removes firmware has to account for that. The name the uploader chose
    lives in `original_filename` and is for display only.

    `size_bytes` and `notes` are dashboard display metadata and never enter the
    signed manifest, which stays `model|version|sha256`.

    `active` is the publish state. A withdrawn version keeps its row so the
    dashboard can show history, but `get_latest_for_model` never offers it.

    `owner_id` is the account that uploaded it, and the axis every other
    account's reads are filtered against. It also widens both uniqueness rules:
    two tenants may each publish their own `ESP32 1.0.0`.

    `download_id` is what `/api/download/{...}` is addressed by, and it is a
    capability rather than a name. The route cannot authenticate, since the
    device has no credential to send, so possession of this string is the only
    thing standing between a caller and the bytes. It is random for that reason:
    a serial number would let anyone holding one of their own links derive
    everyone else's.
    """

    model: str
    version: str
    filename: str
    signature: str
    sha256: str
    size_bytes: int
    owner_id: int | None = None
    download_id: str | None = None
    notes: str | None = None
    active: bool = True
    original_filename: str | None = None
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class Device:
    """A physical ESP32 unit in the field.

    Every field is what the device reported on its last check-in, so the
    dashboard always reads state that is at most one check interval old.
    Nothing here says whether the device is online or behind: both are read off
    `last_seen` and `current_version` at request time by `domain/fleet.py`,
    because a stored status would be wrong the moment a device stops reporting.

    `poll_interval_seconds` is how long the device intends to wait before
    checking in again. It travels with the check-in rather than being a server
    constant, so the one place that number is written down stays `ota.h`.

    Everything past `model` is nullable. Rows written before a field existed
    are never backfilled, since the device overwrites its own row on the next
    check-in anyway.

    `owner_id` is the account the unit belongs to, and what decides whose
    dashboard it appears on. A row only exists because someone registered it,
    so it is always set on anything written since registration landed.

    `secret_hash` is SHA-256 of the value in that unit's `config.json`, and it
    is what a check-in is identified by. It never leaves the server: the
    plaintext is shown once, at registration, and is not recoverable after.

    `enabled` is read on every check-in rather than baked into a token, so
    switching it off stops the next poll rather than waiting for anything to
    expire.
    """

    device_id: str
    model: str
    owner_id: int | None = None
    secret_hash: str | None = None
    enabled: bool = True
    registered_at: datetime | None = None
    current_version: str | None = None
    last_seen: datetime | None = None
    poll_interval_seconds: int | None = None
    rssi: int | None = None
    ip: str | None = None
    last_error: str | None = None
    failed_attempts: int | None = None
    id: int | None = None


@dataclass
class DeviceEvent:
    """One entry in the append-only OTA history.

    Rows are never updated or deleted, so the log stays a record of what the
    fleet did rather than of what it looks like now. `Device` holds the current
    snapshot; this holds how it got there.

    `device_id` is the string the device reports, not a foreign key, so an event
    survives its device row and a download from an unregistered id still lands.
    `from_version` and `to_version` are both optional: a `download` knows only
    where it is going, and a first check-in knows only where it is.
    """

    device_id: str | None
    event_type: EventType
    from_version: str | None = None
    to_version: str | None = None
    id: int | None = None
    created_at: datetime | None = None
