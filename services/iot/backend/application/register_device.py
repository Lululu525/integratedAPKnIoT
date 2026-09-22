"""Register a device, and switch one off.

A unit exists on the server because someone here said it does, which is what
stops any string that posts to `/api/check` from becoming a device.

Registration mints two values: the identifier the unit reports, and the secret
that proves the report came from it. Both go into that unit's `config.json`, so
one LittleFS image does not serve a whole batch.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.models import (
    Device,
    hash_device_secret,
    new_device_id,
    new_device_secret,
)
from ports.repository import DeviceRepository


@dataclass
class RegisteredDevice:
    """The device row, plus the one value that is never stored or shown again.

    Only the hash is kept, so this is the sole moment the secret exists in a
    form anyone can copy. Losing it means registering the unit again.
    """

    device: Device
    secret: str


class RegisterDevice:
    def __init__(self, devices: DeviceRepository) -> None:
        self._devices = devices

    def execute(self, model: str, owner_id: int) -> RegisteredDevice:
        secret = new_device_secret()
        device = self._devices.register(
            Device(
                device_id=new_device_id(),
                model=model,
                owner_id=owner_id,
                secret_hash=hash_device_secret(secret),
            )
        )
        return RegisteredDevice(device=device, secret=secret)


class DeviceNotFound(Exception):
    """Raised when an operation targets a device this account does not have."""


class SetDeviceEnabled:
    def __init__(self, devices: DeviceRepository) -> None:
        self._devices = devices

    def execute(self, device_id: str, owner_id: int, enabled: bool) -> Device:
        # There is no revocation list and nothing to expire. The flag is read
        # on the next check-in, so a unit switched off here stops being served
        # one poll interval later at the latest, and its siblings never notice.
        device = self._devices.set_enabled(device_id, owner_id, enabled)
        if device is None:
            raise DeviceNotFound(device_id)
        return device
