from __future__ import annotations

import pytest
from application.check_update import (
    CheckUpdate,
    CheckUpdateRequest,
    ModelNotFound,
    UnknownDevice,
)
from conftest import FakeDeviceEventRepository, FakeDeviceRepository, FakeFirmwareRepository
from domain.models import Device, DeviceEvent, EventType, Firmware, hash_device_secret

# The account that publishes here, and the one every seeded device belongs to.
OWNER = 1

# One registered unit is the baseline every test starts from, because there is
# no other kind of caller the use case will answer.
DEVICE_ID = "dev-1"
SECRET = "device-secret"


def registered_devices(
    device_id=DEVICE_ID, secret=SECRET, owner_id=OWNER, enabled=True
) -> FakeDeviceRepository:
    devices = FakeDeviceRepository()
    devices.register(
        Device(
            device_id=device_id,
            model="ESP32",
            owner_id=owner_id,
            secret_hash=hash_device_secret(secret),
            enabled=enabled,
        )
    )
    return devices


def make_use_case(rows=(), devices=None, events=None) -> CheckUpdate:
    return CheckUpdate(
        FakeFirmwareRepository(rows),
        devices if devices is not None else registered_devices(),
        events if events is not None else FakeDeviceEventRepository(),
    )


def make_request(
    model="ESP32", version="1.0.0", device_id=DEVICE_ID, device_secret=SECRET, **overrides
) -> CheckUpdateRequest:
    return CheckUpdateRequest(
        model=model,
        version=version,
        device_id=device_id,
        device_secret=device_secret,
        **overrides,
    )


def make_firmware(
    model="ESP32", version="1.1.0", firmware_id=7, active=True, owner_id=OWNER
) -> Firmware:
    return Firmware(
        owner_id=owner_id,
        # Distinguishable from the row id on purpose: the download link is a
        # separate random handle, and a test asserting the id would pass on a
        # route that had gone back to exposing the primary key.
        download_id=f"link-{firmware_id}",
        model=model,
        version=version,
        filename=f"{firmware_id}_firmware.bin",
        signature="c2ln",
        sha256="a" * 64,
        size_bytes=1,
        id=firmware_id,
        active=active,
    )


def test_execute_raises_when_model_unknown():
    use_case = make_use_case()

    with pytest.raises(ModelNotFound):
        use_case.execute(make_request())


def test_execute_reports_no_update_when_current_version_is_latest():
    latest = make_firmware(version="1.0.0")
    use_case = make_use_case([latest])

    result = use_case.execute(make_request())

    assert result.update_available is False
    assert result.version is None
    assert result.download_url is None


def test_execute_reports_no_update_when_latest_version_is_deactivated():
    """A withdrawn 1.0.1 must not be offered, but the model still exists.

    `get_latest_for_model` falls back to the newest active row, so the device
    on 1.0.0 gets a plain no-update, not a 403 and not a download.
    """
    older = make_firmware(version="1.0.0", firmware_id=1)
    withdrawn = make_firmware(version="1.0.1", firmware_id=2, active=False)
    use_case = make_use_case([older, withdrawn])

    result = use_case.execute(make_request(version="1.0.0"))

    assert result.update_available is False
    assert result.version is None
    assert result.download_url is None


def test_execute_raises_when_every_version_is_inactive():
    use_case = make_use_case(
        [
            make_firmware(version="1.0.0", firmware_id=1, active=False),
            make_firmware(version="1.0.1", firmware_id=2, active=False),
        ]
    )

    with pytest.raises(ModelNotFound):
        use_case.execute(make_request())


def test_execute_reports_update_with_signature_and_download_url():
    latest = make_firmware(version="1.2.0", firmware_id=42)
    use_case = make_use_case([latest])

    result = use_case.execute(make_request(version="1.1.0"))

    assert result.update_available is True
    assert result.model == "ESP32"
    assert result.version == "1.2.0"
    assert result.signature == latest.signature
    assert result.download_url == "/api/download/link-42?device_id=dev-1"


def test_execute_checks_the_requested_model_only():
    other_model_latest = make_firmware(model="ESP32-S3", version="9.9.9")
    use_case = make_use_case([other_model_latest])

    with pytest.raises(ModelNotFound):
        use_case.execute(make_request())


def test_execute_records_the_checkin():
    devices = registered_devices()
    use_case = make_use_case([make_firmware(version="1.1.0")], devices)

    use_case.execute(make_request())

    recorded = devices.devices[DEVICE_ID]
    assert recorded.model == "ESP32"
    assert recorded.current_version == "1.0.0"
    assert recorded.last_seen is not None


def test_execute_records_reported_telemetry():
    devices = registered_devices()
    use_case = make_use_case([make_firmware(version="1.1.0")], devices)

    use_case.execute(make_request(poll_interval_seconds=6, rssi=-52, ip="10.0.4.11"))

    recorded = devices.devices[DEVICE_ID]
    assert recorded.poll_interval_seconds == 6
    assert recorded.rssi == -52
    assert recorded.ip == "10.0.4.11"


def test_execute_accepts_a_checkin_carrying_no_telemetry():
    """The route requires the telemetry; the use case never has.

    Keeping this end open is what lets the deployment question (what our one
    device must send) move without touching the update decision.
    """
    devices = registered_devices()
    use_case = make_use_case([make_firmware(version="1.1.0")], devices)

    result = use_case.execute(make_request())

    assert result.update_available is True
    assert devices.devices[DEVICE_ID].poll_interval_seconds is None


def test_an_unregistered_device_is_refused_and_leaves_no_record():
    """The whole of what registration buys.

    Before it, any string posted here became a device row on whichever
    account's list the model name happened to match, and a request answered 403
    for an unknown model had already written one.
    """
    devices = FakeDeviceRepository()
    use_case = make_use_case([make_firmware(version="1.1.0")], devices)

    with pytest.raises(UnknownDevice):
        use_case.execute(make_request(device_id="never-registered"))

    assert devices.devices == {}


def test_a_wrong_secret_is_refused():
    use_case = make_use_case([make_firmware(version="1.1.0")])

    with pytest.raises(UnknownDevice):
        use_case.execute(make_request(device_secret="not-the-secret"))


def test_a_disabled_device_is_refused_on_its_next_poll():
    """No token to expire and no revocation list: the flag is read every time."""
    devices = registered_devices()
    use_case = make_use_case([make_firmware(version="1.1.0")], devices)
    use_case.execute(make_request())

    devices.set_enabled(DEVICE_ID, OWNER, False)

    with pytest.raises(UnknownDevice):
        use_case.execute(make_request())


def test_disabling_one_device_leaves_its_siblings_working():
    devices = registered_devices()
    devices.register(
        Device(
            device_id="dev-2",
            model="ESP32",
            owner_id=OWNER,
            secret_hash=hash_device_secret("other-secret"),
        )
    )
    use_case = make_use_case([make_firmware(version="1.1.0")], devices)

    devices.set_enabled(DEVICE_ID, OWNER, False)

    result = use_case.execute(make_request(device_id="dev-2", device_secret="other-secret"))
    assert result.update_available is True


def test_a_refused_check_in_writes_nothing_to_the_device_row():
    devices = registered_devices()
    use_case = make_use_case([make_firmware(version="1.1.0")], devices)

    with pytest.raises(UnknownDevice):
        use_case.execute(make_request(device_secret="wrong"))

    assert devices.devices[DEVICE_ID].current_version is None
    assert devices.devices[DEVICE_ID].last_seen is None


def test_execute_records_checkin_even_for_unknown_model():
    devices = registered_devices()
    use_case = make_use_case([], devices)

    with pytest.raises(ModelNotFound):
        use_case.execute(make_request())

    assert DEVICE_ID in devices.devices


def test_a_device_moving_up_a_version_records_a_success():
    devices = registered_devices()
    events = FakeDeviceEventRepository()
    use_case = make_use_case([make_firmware(version="1.2.0")], devices, events)

    use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))
    use_case.execute(make_request(version="1.2.0", device_id=DEVICE_ID))

    success = next(e for e in events.events if e.event_type is EventType.SUCCESS)
    assert (success.from_version, success.to_version) == ("1.0.0", "1.2.0")


def test_a_device_coming_back_on_an_older_version_records_a_rollback():
    """The device rolled itself back, so the server only ever sees the result.

    Nothing in the protocol reports a failed flash after the reboot; the
    version going backwards between two check-ins is the whole signal.
    """
    devices = registered_devices()
    events = FakeDeviceEventRepository()
    use_case = make_use_case([make_firmware(version="1.2.0")], devices, events)

    use_case.execute(make_request(version="1.1.0", device_id=DEVICE_ID))
    use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))

    rollback = next(e for e in events.events if e.event_type is EventType.ROLLBACK)
    assert (rollback.from_version, rollback.to_version) == ("1.1.0", "1.0.0")


def test_a_check_event_is_recorded_only_when_an_update_is_offered():
    """Devices poll every few seconds. A row per check-in is tens of thousands
    a day per device, all repeating what `last_seen` already says."""
    devices = registered_devices()
    events = FakeDeviceEventRepository()
    use_case = make_use_case([make_firmware(version="1.0.0")], devices, events)

    use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))
    use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))

    assert events.types() == []


def test_a_standing_offer_is_recorded_once_not_once_per_poll():
    """The same offer stands until the device acts on it, and it polls every
    few seconds. Recording each one is the volume this table cannot carry."""
    events = FakeDeviceEventRepository()
    use_case = make_use_case([make_firmware(version="1.2.0")], registered_devices(), events)

    for _ in range(5):
        use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))

    assert events.types() == [EventType.CHECK]


def test_an_offer_after_the_device_did_something_is_recorded_again():
    """A download in between means the offer that follows it is a new one."""
    events = FakeDeviceEventRepository()
    use_case = make_use_case([make_firmware(version="1.2.0")], registered_devices(), events)

    use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))
    events.add(DeviceEvent(device_id=DEVICE_ID, event_type=EventType.DOWNLOAD, to_version="1.2.0"))
    use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))

    assert events.types() == [EventType.CHECK, EventType.DOWNLOAD, EventType.CHECK]


def test_an_offer_records_a_check_naming_both_versions():
    devices = registered_devices()
    events = FakeDeviceEventRepository()
    use_case = make_use_case([make_firmware(version="1.2.0")], devices, events)

    use_case.execute(make_request(version="1.0.0", device_id=DEVICE_ID))

    assert events.types() == [EventType.CHECK]
    assert (events.events[0].from_version, events.events[0].to_version) == ("1.0.0", "1.2.0")


def test_a_refused_check_in_records_no_event():
    events = FakeDeviceEventRepository()
    use_case = make_use_case([make_firmware(version="1.2.0")], registered_devices(), events)

    with pytest.raises(UnknownDevice):
        use_case.execute(make_request(version="1.0.0", device_secret="wrong"))

    assert events.types() == []


def test_the_download_url_carries_the_reported_device_id():
    use_case = make_use_case([make_firmware(version="1.2.0", firmware_id=7)])

    result = use_case.execute(make_request(version="1.0.0"))

    assert result.download_url == "/api/download/link-7?device_id=dev-1"


def test_a_registered_device_is_offered_only_its_own_account_s_firmware():
    """Whose firmware to look in comes from the device's row, not from the model.

    Two accounts publishing under one model name is the case ownership creates,
    and picking by model alone would hand one tenant's build to the other's
    fleet.
    """
    use_case = make_use_case(
        [
            make_firmware(version="1.1.0", firmware_id=1, owner_id=OWNER),
            make_firmware(version="9.9.9", firmware_id=2, owner_id=2),
        ]
    )

    result = use_case.execute(make_request())

    assert result.version == "1.1.0"


def test_a_registered_device_gets_no_update_when_its_own_account_has_none():
    """Another account's newer build is not an update, it is someone else's."""
    use_case = make_use_case([make_firmware(version="9.9.9", firmware_id=2, owner_id=2)])

    with pytest.raises(ModelNotFound):
        use_case.execute(make_request())


def test_a_check_in_never_moves_a_device_between_accounts():
    """The body is the device describing itself, and this is not its to say."""
    devices = registered_devices()
    use_case = make_use_case([make_firmware(owner_id=OWNER)], devices=devices)

    use_case.execute(make_request())

    assert devices.get_by_device_id(DEVICE_ID).owner_id == OWNER


def test_a_check_in_cannot_switch_a_disabled_device_back_on():
    devices = registered_devices(enabled=False)
    use_case = make_use_case([make_firmware(owner_id=OWNER)], devices=devices)

    with pytest.raises(UnknownDevice):
        use_case.execute(make_request())

    assert devices.get_by_device_id(DEVICE_ID).enabled is False
