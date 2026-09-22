"""Decide whether a device should update.

Given a device's model and its current version, finds the latest firmware for
that model and returns its download details only when it is strictly newer.
A check-in that carries a device id is also recorded, which is what feeds the
dashboard's device page.

Which account's firmware to look in comes from the device's own row, found by
the identifier it reports and confirmed by the secret it sends. The secret is
identity and routing at once: knowing which unit is calling is knowing which
tenant it belongs to, which is what lets two tenants publish under one model
name without either seeing the other's builds.

It identifies and never authorizes. Nothing a check-in carries changes anything
but the device's own reported state, so a secret pulled out of a teardown reads
that unit's updates and cannot publish, withdraw, or touch another unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

from domain import ota_history, signing
from domain.models import Device, DeviceEvent, EventType, secret_matches
from ports.repository import DeviceEventRepository, DeviceRepository, FirmwareRepository


@dataclass
class CheckUpdateRequest:
    """One check-in. Everything the device chooses to say about itself.

    `device_id` and `device_secret` decide who is asking, `model` and `version`
    steer the answer, and the rest is recorded and never read here, which is
    why a device that reports none of the telemetry still gets a correct
    update decision.

    The telemetry is optional here and required on `api.routes.CheckRequest`.
    That gap is deliberate, not an oversight to tidy up: what the fleet must
    send is a deployment question the HTTP layer answers, while the decision
    this class makes has never needed any of it.
    """

    model: str
    version: str
    device_id: str
    device_secret: str
    poll_interval_seconds: int | None = None
    rssi: int | None = None
    ip: str | None = None
    last_error: str | None = None
    failed_attempts: int | None = None


@dataclass
class CheckUpdateResult:
    update_available: bool
    model: str | None = None
    version: str | None = None
    signature: str | None = None
    download_url: str | None = None


class ModelNotFound(Exception):
    """Raised when the requested model has no firmware on record (the API returns HTTP 403)."""


class ModelMismatch(Exception):
    """Raised when a check-in claims a model that is not the one it registered as.

    Carries both so the server log can name them; the route answers a bare 403
    either way.
    """

    def __init__(self, registered: str, claimed: str) -> None:
        super().__init__(f"registered as {registered!r}, claimed {claimed!r}")
        self.registered = registered
        self.claimed = claimed


class UnknownDevice(Exception):
    """Raised when a check-in cannot be attributed to a registered, enabled device.

    One exception for three causes: no such device, a secret that does not
    match, and a device that has been switched off. They are answered
    identically (HTTP 401) so the route cannot be used to find out which device
    identifiers exist.
    """


class CheckUpdate:
    def __init__(
        self,
        repository: FirmwareRepository,
        devices: DeviceRepository,
        events: DeviceEventRepository,
    ) -> None:
        self._repo = repository
        self._devices = devices
        self._events = events

    def _offer_is_news(self, device_id: str, current: str, offered: str) -> bool:
        """Whether this offer says anything the log does not already hold.

        The same offer stands on every poll until the device acts on it, so a
        device that cannot flash would otherwise write a row every few seconds
        forever. Only the most recent event is consulted: a download, success
        or rollback in between means the device did something, and the offer
        that follows is a new one.
        """
        recent = self._events.list_for_device(device_id, limit=1)
        if not recent:
            return True
        last = recent[0]
        return not (
            last.event_type is EventType.CHECK
            and last.from_version == current
            and last.to_version == offered
        )

    def execute(self, req: CheckUpdateRequest) -> CheckUpdateResult:
        # Identity first, before anything is read or written. A caller that
        # cannot prove which device it is gets no answer and leaves no trace,
        # which is the whole of what registration buys.
        device = self._devices.get_by_device_id(req.device_id)
        # An ownerless row cannot get past the secret check either, but it is
        # named here because everything below needs an account to scope to.
        if device is None or not device.enabled or device.owner_id is None:
            raise UnknownDevice(req.device_id)
        if not secret_matches(req.device_secret, device.secret_hash):
            raise UnknownDevice(req.device_id)
        # Before the firmware lookup, not after. Compared afterwards, the
        # answer would differ for a model this account publishes and one it
        # does not, and a single teardown would read the account's whole model
        # catalogue by trying names. Refused here, the response depends only on
        # what the caller already knows about its own unit.
        if req.model != device.model:
            raise ModelMismatch(device.model, req.model)

        # Read before the check-in overwrites it. This is the only moment the
        # previous reported version is still available, and the whole event log
        # is built out of the difference between it and what just arrived.
        previous_version = device.current_version
        owner_id = device.owner_id

        # Recorded before the firmware lookup, so a device whose model has no
        # published firmware yet still shows a current check-in.
        self._devices.record_checkin(
            Device(
                device_id=req.device_id,
                model=device.model,
                owner_id=owner_id,
                current_version=req.version,
                last_seen=datetime.now(timezone.utc),
                poll_interval_seconds=req.poll_interval_seconds,
                rssi=req.rssi,
                ip=req.ip,
                last_error=req.last_error,
                failed_attempts=req.failed_attempts,
            )
        )
        transition = ota_history.classify_version_change(previous_version, req.version)
        if transition:
            self._events.add(
                DeviceEvent(
                    device_id=req.device_id,
                    event_type=transition,
                    from_version=previous_version,
                    to_version=req.version,
                )
            )

        latest = self._repo.get_latest_for_model(device.model, owner_id)
        if latest is None:
            raise ModelNotFound(req.model)

        if not signing.compare_version(latest.version, req.version):
            return CheckUpdateResult(update_available=False)

        if self._offer_is_news(req.device_id, req.version, latest.version):
            self._events.add(
                DeviceEvent(
                    device_id=req.device_id,
                    event_type=EventType.CHECK,
                    from_version=req.version,
                    to_version=latest.version,
                )
            )

        return CheckUpdateResult(
            update_available=True,
            model=device.model,
            version=latest.version,
            signature=latest.signature,
            # The device follows this verbatim (`ota.cpp:392`), so the id it
            # already reported rides along and the download can be attributed
            # without the firmware sending anything new. The path carries the
            # random `download_id` rather than the row's primary key, and the
            # device is none the wiser: it concatenates whatever arrives here.
            download_url=(f"/api/download/{latest.download_id}?device_id={quote(req.device_id)}"),
        )
