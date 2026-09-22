"""Summarize the fleet for the device page's cards.

Reads the same rows `GET /api/devices` returns and tallies them, rather than
handing the raw list to a browser that counts for itself. Counting client-side
would need the firmware list as well and a second copy of the version compare,
which is the drift the server is in a position to avoid: it holds both halves.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain import fleet
from ports.repository import DeviceRepository, FirmwareRepository


class DeviceStats:
    def __init__(self, devices: DeviceRepository, firmware: FirmwareRepository) -> None:
        self._devices = devices
        self._firmware = firmware

    def execute(self, owner_id: int) -> fleet.FleetStats:
        devices = self._devices.list_all(owner_id)

        # Looked up per model rather than off a full firmware list, so the
        # answer comes from the same `get_latest_for_model` a check-in gets,
        # including its active-only filter. A withdrawn newest version is then
        # not something any device counts as behind.
        latest_versions: dict[str, str | None] = {}
        for model in {d.model for d in devices}:
            latest = self._firmware.get_latest_for_model(model, owner_id)
            latest_versions[model] = latest.version if latest else None

        # One clock reading for the whole tally, matching `GET /api/devices`.
        return fleet.summarize(devices, latest_versions, datetime.now(timezone.utc))
