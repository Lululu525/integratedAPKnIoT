"""Assembles what the route handlers need and exposes it as FastAPI dependencies.

Opens a database session per request and builds the repositories, storage and
use cases on top of it. Resolving a bearer token into an account is not here:
that is fastapi-users' job and lives in `api/auth.py`, which depends on this
module for the account store. Nothing here may import it back.
"""

from __future__ import annotations

from collections.abc import Iterator

from application.check_update import CheckUpdate
from application.deactivate_firmware import DeactivateFirmware
from application.device_stats import DeviceStats
from application.register_device import RegisterDevice, SetDeviceEnabled
from application.session import Session as UserSession
from application.upload_firmware import UploadFirmware
from config import Settings, get_settings
from domain.models import User
from fastapi import Depends
from fastapi_users.db import BaseUserDatabase
from infrastructure.db import SessionLocal
from infrastructure.local_storage import LocalStorage
from infrastructure.sqlite_repo import (
    SqliteDeviceEventRepository,
    SqliteDeviceRepository,
    SqliteFirmwareRepository,
    SqliteRefreshTokenRepository,
    SqliteUserRepository,
)
from infrastructure.user_db import SyncUserDatabase
from ports.repository import (
    DeviceEventRepository,
    DeviceRepository,
    FirmwareRepository,
    RefreshTokenRepository,
    UserRepository,
)
from ports.storage import StorageBackend
from sqlalchemy.orm import Session


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_firmware_repository(db: Session = Depends(get_db)) -> FirmwareRepository:
    return SqliteFirmwareRepository(db)


def get_user_repository(db: Session = Depends(get_db)) -> UserRepository:
    return SqliteUserRepository(db)


def get_refresh_token_repository(db: Session = Depends(get_db)) -> RefreshTokenRepository:
    return SqliteRefreshTokenRepository(db)


def get_user_db(
    repo: UserRepository = Depends(get_user_repository),
) -> BaseUserDatabase[User, int]:
    return SyncUserDatabase(repo)


def get_session(
    tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    users: UserRepository = Depends(get_user_repository),
    settings: Settings = Depends(get_settings),
) -> UserSession:
    return UserSession(tokens, users, settings.refresh_expires_days)


def get_device_repository(db: Session = Depends(get_db)) -> DeviceRepository:
    return SqliteDeviceRepository(db)


def get_device_event_repository(db: Session = Depends(get_db)) -> DeviceEventRepository:
    return SqliteDeviceEventRepository(db)


def get_storage(settings: Settings = Depends(get_settings)) -> StorageBackend:
    return LocalStorage(settings.firmware_dir)


def get_check_update(
    repo: FirmwareRepository = Depends(get_firmware_repository),
    devices: DeviceRepository = Depends(get_device_repository),
    events: DeviceEventRepository = Depends(get_device_event_repository),
) -> CheckUpdate:
    return CheckUpdate(repo, devices, events)


def get_register_device(
    devices: DeviceRepository = Depends(get_device_repository),
) -> RegisterDevice:
    return RegisterDevice(devices)


def get_set_device_enabled(
    devices: DeviceRepository = Depends(get_device_repository),
) -> SetDeviceEnabled:
    return SetDeviceEnabled(devices)


def get_device_stats(
    devices: DeviceRepository = Depends(get_device_repository),
    firmware: FirmwareRepository = Depends(get_firmware_repository),
) -> DeviceStats:
    return DeviceStats(devices, firmware)


def get_upload_firmware(
    repo: FirmwareRepository = Depends(get_firmware_repository),
    storage: StorageBackend = Depends(get_storage),
) -> UploadFirmware:
    return UploadFirmware(repo, storage)


def get_deactivate_firmware(
    repo: FirmwareRepository = Depends(get_firmware_repository),
) -> DeactivateFirmware:
    return DeactivateFirmware(repo)
