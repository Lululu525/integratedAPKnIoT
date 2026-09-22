"""HTTP-level tests for the device protocol and read-only routes.

The admin gate on `/firmware/upload` is covered in `test_auth_routes.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from api.auth import current_active_user
from api.deps import (
    get_check_update,
    get_device_event_repository,
    get_device_repository,
    get_device_stats,
    get_firmware_repository,
    get_storage,
)
from application.check_update import CheckUpdate
from application.device_stats import DeviceStats
from conftest import (
    FakeDeviceEventRepository,
    FakeDeviceRepository,
    FakeFirmwareRepository,
    FakeStorage,
)
from domain.models import Device, EventType, Firmware, User, hash_device_secret
from fastapi.testclient import TestClient
from main import app
from ports.storage import CHUNK_SIZE

# The account these tests sign in as, and the owner of everything they seed.
OWNER = 1


def make_account() -> User:
    return User(email="op@example.com", hashed_password="x", id=OWNER)


def make_firmware(
    model="ESP32",
    version="1.0.0",
    firmware_id=1,
    original_filename="main.ino.bin",
    notes=None,
    size_bytes=1300234,
) -> Firmware:
    return Firmware(
        owner_id=OWNER,
        # Not the row id. The route is addressed by the random handle, so a
        # test using the id would still pass against a route that exposed it.
        download_id=f"link-{firmware_id}",
        model=model,
        version=version,
        filename=f"{firmware_id}_firmware.bin",
        original_filename=original_filename,
        signature="c2ln",
        sha256="a" * 64,
        size_bytes=size_bytes,
        notes=notes,
        id=firmware_id,
        # `id` and `created_at` are only ever None before the row is written,
        # and every route reads rows that already are.
        created_at=datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


DEVICE_ID = "dev-1"
DEVICE_SECRET = "device-secret"


def registered_devices(device_id=DEVICE_ID, secret=DEVICE_SECRET, **overrides):
    """One registered unit, which is the only kind `/api/check` will answer."""
    devices = FakeDeviceRepository()
    devices.register(
        Device(
            device_id=device_id,
            model="ESP32",
            owner_id=OWNER,
            secret_hash=hash_device_secret(secret),
            **overrides,
        )
    )
    return devices


def seed_checked_in(devices, device_id: str, **reported) -> None:
    """A registered unit that has since checked in, which is two steps now.

    Registration is the only thing that creates a row, so a test that only
    recorded a check-in would be asserting against an empty repository.
    """
    devices.register(
        Device(
            device_id=device_id,
            model=reported.get("model", "ESP32"),
            owner_id=OWNER,
            secret_hash=hash_device_secret(DEVICE_SECRET),
        )
    )
    devices.record_checkin(Device(device_id=device_id, **reported))


def check_payload(**overrides) -> dict:
    """A well-formed check-in. `ota.cpp` always sends the telemetry, so tests do too."""
    return {
        "model": "ESP32",
        "version": "1.0.0",
        "device_id": DEVICE_ID,
        "device_secret": DEVICE_SECRET,
        "poll_interval_seconds": 6,
        "rssi": -52,
        "ip": "10.0.4.11",
    } | overrides


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_check_update_returns_403_for_unknown_model(client):
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository(), registered_devices(), FakeDeviceEventRepository()
    )

    response = client.post("/api/check", json=check_payload())

    assert response.status_code == 403


def test_check_update_reports_no_update_when_current_is_latest(client):
    latest = make_firmware(version="1.0.0")
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), registered_devices(), FakeDeviceEventRepository()
    )

    response = client.post("/api/check", json=check_payload())

    assert response.status_code == 200
    assert response.json() == {"update_available": False}


def test_check_update_reports_available_update_with_download_url(client):
    latest = make_firmware(version="1.2.0", firmware_id=42)
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), registered_devices(), FakeDeviceEventRepository()
    )

    response = client.post("/api/check", json=check_payload(version="1.1.0"))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "update_available": True,
        "version": "1.2.0",
        "signature": "c2ln",
        # The reported id rides back on the URL the device follows verbatim,
        # which is what lets a download be attributed with no device change.
        "download_url": "/api/download/link-42?device_id=dev-1",
    }


def test_check_response_carries_only_what_the_device_reads(client):
    """`CheckUpdateResult` also holds `model`, which was never on the wire.

    The response model is what keeps that true: a field added to the use case's
    result no longer reaches a device by default.
    """
    latest = make_firmware(version="1.2.0", firmware_id=42)
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), registered_devices(), FakeDeviceEventRepository()
    )

    body = client.post("/api/check", json=check_payload(version="1.1.0")).json()

    assert set(body) == {"update_available", "version", "signature", "download_url"}


def test_check_update_records_device_checkin(client):
    latest = make_firmware(version="1.2.0", firmware_id=42)
    devices = registered_devices()
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), devices, FakeDeviceEventRepository()
    )

    client.post("/api/check", json=check_payload(version="1.1.0"))

    assert devices.devices["dev-1"].current_version == "1.1.0"


def test_check_update_records_a_reported_update_failure(client):
    latest = make_firmware(version="1.2.0", firmware_id=42)
    devices = registered_devices()
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), devices, FakeDeviceEventRepository()
    )

    client.post(
        "/api/check",
        json=check_payload(last_error="signature", failed_attempts=2),
    )

    assert devices.devices["dev-1"].last_error == "signature"
    assert devices.devices["dev-1"].failed_attempts == 2


def test_check_update_accepts_a_device_reporting_no_failure(client):
    """The field is optional so firmware built before it exists still checks in.

    Required telemetry would 422 the whole fleet off the dashboard over a
    field that only means something after an update has already gone wrong.
    """
    latest = make_firmware(version="1.2.0", firmware_id=42)
    devices = registered_devices()
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), devices, FakeDeviceEventRepository()
    )

    response = client.post("/api/check", json=check_payload())

    assert response.status_code == 200
    assert devices.devices["dev-1"].last_error is None


def test_check_update_clears_a_failure_the_device_stopped_reporting(client):
    """A recovered device stops sending the field, and the row has to follow.

    The device resends its error on every check-in until a flash succeeds, so
    an absent one means it is gone, not that nothing was said this time.
    """
    latest = make_firmware(version="1.2.0", firmware_id=42)
    devices = registered_devices()
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), devices, FakeDeviceEventRepository()
    )

    client.post(
        "/api/check",
        json=check_payload(last_error="signature", failed_attempts=2),
    )
    client.post("/api/check", json=check_payload())

    assert devices.devices["dev-1"].last_error is None
    assert devices.devices["dev-1"].failed_attempts is None


def test_check_update_rejects_an_oversized_error_token(client):
    """`/api/check` is unauthenticated, so the one free-text field is bounded."""
    latest = make_firmware(version="1.2.0", firmware_id=42)
    app.dependency_overrides[get_check_update] = lambda: CheckUpdate(
        FakeFirmwareRepository([latest]), registered_devices(), FakeDeviceEventRepository()
    )

    response = client.post(
        "/api/check",
        json=check_payload(last_error="x" * 65),
    )

    assert response.status_code == 422


def test_download_firmware_returns_404_for_unknown_id(client):
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository()
    app.dependency_overrides[get_storage] = lambda: FakeStorage()
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()

    response = client.get("/api/download/no-such-link")

    assert response.status_code == 404


def test_download_firmware_returns_404_when_file_missing_from_storage(client):
    firmware = make_firmware(firmware_id=1)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: FakeStorage()  # file was never stored
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()

    response = client.get("/api/download/link-1")

    assert response.status_code == 404


def test_download_firmware_returns_binary_with_expected_headers(client):
    firmware = make_firmware(firmware_id=1)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: FakeStorage(
        {firmware.filename: b"binary contents"}
    )
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()

    response = client.get("/api/download/link-1")

    assert response.status_code == 200
    assert response.content == b"binary contents"
    assert response.headers["content-type"] == "application/octet-stream"
    # The blob is addressed by hash; the browser is offered the uploader's name.
    assert firmware.original_filename in response.headers["content-disposition"]


def test_download_firmware_declares_the_length_the_device_checks_against(client):
    """`ota.cpp` detects a truncated body only by comparing Content-Length.

    A streaming response carries no length unless one is set, so losing this
    header does not fail anything visible here: the device would accept a short
    image and flash it, and only its own re-hash would catch it.
    """
    body = b"binary contents"
    firmware = make_firmware(firmware_id=1, size_bytes=len(body))
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: FakeStorage({firmware.filename: body})
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()

    response = client.get("/api/download/link-1")

    assert response.headers["content-length"] == str(len(body))
    assert "transfer-encoding" not in response.headers


def test_download_firmware_declares_the_stored_length_not_the_file_on_disk(client):
    """A blob truncated under the row must disagree with what the server promised.

    Reading the length off the file instead would let a half-written blob
    describe itself, and the mismatch the device relies on would never happen.
    """
    firmware = make_firmware(firmware_id=1, size_bytes=1300234)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: FakeStorage({firmware.filename: b"truncated"})
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()

    response = client.get("/api/download/link-1")

    assert response.headers["content-length"] == "1300234"


def test_download_firmware_reads_the_blob_in_pieces(client):
    """The whole image is never resident, which is the point of the change."""
    body = b"x" * (CHUNK_SIZE * 2 + 7)
    firmware = make_firmware(firmware_id=1, size_bytes=len(body))
    storage = FakeStorage({firmware.filename: body})
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()

    response = client.get("/api/download/link-1")

    assert response.content == body
    assert len(list(storage.iter_chunks(firmware.filename))) == 3


@pytest.mark.parametrize(
    "original_filename",
    ["韌體v1.bin", 'we"ird.bin', "line\nbreak.bin"],
    ids=["non-ascii", "quote", "control-char"],
)
def test_download_firmware_survives_a_hostile_upload_name(client, original_filename):
    """Header values are latin-1 encoded, so an unescaped name is a 500, not a cosmetic bug.

    A row that cannot be downloaded is worse than it sounds: `/api/check` keeps
    naming it as latest, so every device of that model retries forever.
    """
    firmware = make_firmware(firmware_id=1, original_filename=original_filename)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: FakeStorage(
        {firmware.filename: b"binary contents"}
    )
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()

    response = client.get("/api/download/link-1")

    assert response.status_code == 200
    assert response.content == b"binary contents"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="')
    # The fallback carries no character that could end the quoted string early
    # or split the header; the UTF-8 form keeps the real name recoverable.
    fallback = disposition.split('"')[1]
    assert fallback.isascii() and fallback.isprintable()
    assert "filename*=UTF-8''" in disposition


def test_firmware_list_requires_login(client):
    response = client.get("/api/firmware/list")

    assert response.status_code == 401


def test_firmware_list_api_returns_all_firmware_as_json(client):
    firmware = make_firmware(firmware_id=1)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[current_active_user] = lambda: make_account()

    response = client.get("/api/firmware/list")

    assert response.status_code == 200
    assert response.json()[0]["model"] == "ESP32"
    assert response.json()[0]["id"] == 1


def test_firmware_list_api_returns_size_and_notes(client):
    firmware = make_firmware(firmware_id=1, notes="Fix SNTP retry storm")
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[current_active_user] = lambda: make_account()

    response = client.get("/api/firmware/list")

    assert response.status_code == 200
    assert response.json()[0]["size_bytes"] == 1300234
    assert response.json()[0]["notes"] == "Fix SNTP retry storm"


def test_firmware_list_api_carries_active_flag(client):
    firmware = make_firmware(firmware_id=1)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[current_active_user] = lambda: make_account()

    response = client.get("/api/firmware/list")

    assert response.status_code == 200
    assert response.json()[0]["active"] is True


def test_firmware_list_created_at_carries_a_utc_offset(client):
    """An ISO string with no offset is read as local time by a browser, which
    shows upload times shifted against the device times beside them.
    """
    firmware = make_firmware(firmware_id=1)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[current_active_user] = lambda: make_account()

    response = client.get("/api/firmware/list")

    assert response.json()[0]["created_at"] == "2026-07-15T12:00:00Z"


def test_device_list_derives_online_from_the_reported_interval(client):
    """`online` is the one key with no column behind it.

    It is answered against the clock at request time, so a device that checked
    in moments ago reads as online without anything having written a status.
    """
    devices = FakeDeviceRepository()
    seed_checked_in(
        devices,
        "aa:bb:cc",
        model="ESP32",
        current_version="1.0.0",
        last_seen=datetime.now(timezone.utc),
        poll_interval_seconds=6,
        rssi=-52,
        ip="10.0.4.11",
    )
    app.dependency_overrides[get_device_repository] = lambda: devices
    app.dependency_overrides[current_active_user] = lambda: make_account()

    body = client.get("/api/devices").json()

    assert body[0]["online"] is True
    assert body[0]["rssi"] == -52
    assert body[0]["ip"] == "10.0.4.11"
    assert body[0]["poll_interval_seconds"] == 6


def test_device_list_requires_login(client):
    response = client.get("/api/devices")

    assert response.status_code == 401


def test_device_list_returns_devices_with_utc_last_seen(client):
    # The repository is what re-attaches the offset SQLite drops, so a fake
    # standing in for it hands back an aware datetime too.
    devices = FakeDeviceRepository()
    seed_checked_in(
        devices,
        "aa:bb:cc",
        model="ESP32",
        current_version="1.0.0",
        last_seen=datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    app.dependency_overrides[get_device_repository] = lambda: devices
    app.dependency_overrides[current_active_user] = lambda: make_account()

    response = client.get("/api/devices")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "device_id": "aa:bb:cc",
            "model": "ESP32",
            "current_version": "1.0.0",
            # FastAPI's encoder writes UTC as `Z`, where the hand-rolled
            # `isoformat()` this replaced wrote `+00:00`. Both parse the same.
            "last_seen": "2026-07-15T12:00:00Z",
            # A device on firmware from before it reported these. `online` is
            # null rather than false: nothing here says the device is gone.
            "poll_interval_seconds": None,
            "rssi": None,
            "ip": None,
            "last_error": None,
            "failed_attempts": None,
            "enabled": True,
            "online": None,
        }
    ]


def seen(seconds_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)


def test_device_stats_tallies_the_fleet(client):
    """The cards read one endpoint, so the counts cannot disagree with each other.

    Counting in the browser would need the firmware list too, and a second copy
    of the version compare to go with it.
    """
    devices = FakeDeviceRepository()
    for index, (version, last_seen, interval) in enumerate(
        [
            ("1.0.0", seen(1), 6),  # online, behind 1.2.0
            ("1.2.0", seen(1), 6),  # online, current
            ("1.0.0", seen(600), 6),  # offline, behind
            ("1.0.0", None, 6),  # never checked in, unknown, behind
            ("1.0.0", seen(1), 6),  # online, model has nothing published
        ],
        start=1,
    ):
        seed_checked_in(
            devices,
            f"dev-{index}",
            model="ESP32" if index < 5 else "ESP32-Unpublished",
            current_version=version,
            last_seen=last_seen,
            poll_interval_seconds=interval,
        )
    firmware = FakeFirmwareRepository([make_firmware(version="1.2.0")])
    app.dependency_overrides[get_device_stats] = lambda: DeviceStats(devices, firmware)
    app.dependency_overrides[current_active_user] = lambda: make_account()

    response = client.get("/api/devices/stats")

    assert response.status_code == 200
    assert response.json() == {
        "total": 5,
        "online": 3,
        "offline": 1,
        "unknown": 1,
        "behind_latest": 3,
    }


def test_device_stats_ignores_a_withdrawn_newest_version(client):
    """Withdrawing is what an admin does to a bad release.

    The card must stop counting devices as behind it at the same moment
    `/api/check` stops offering it, or the dashboard asks for an update the
    fleet will never be given.
    """
    devices = registered_devices()
    devices.record_checkin(
        Device(
            owner_id=OWNER,
            id=1,
            device_id="dev-1",
            model="ESP32",
            current_version="1.0.0",
            last_seen=seen(1),
            poll_interval_seconds=6,
        )
    )
    withdrawn = make_firmware(version="1.3.0", firmware_id=2)
    withdrawn.active = False
    firmware = FakeFirmwareRepository([make_firmware(version="1.0.0"), withdrawn])
    app.dependency_overrides[get_device_stats] = lambda: DeviceStats(devices, firmware)
    app.dependency_overrides[current_active_user] = lambda: make_account()

    body = client.get("/api/devices/stats").json()

    assert body["behind_latest"] == 0


def test_device_stats_requires_login(client):
    response = client.get("/api/devices/stats")

    assert response.status_code == 401


def test_download_records_an_attributed_event(client):
    firmware = make_firmware(firmware_id=1, version="1.2.0")
    events = FakeDeviceEventRepository()
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: FakeStorage(
        {firmware.filename: b"binary contents"}
    )
    app.dependency_overrides[get_device_event_repository] = lambda: events

    client.get("/api/download/link-1?device_id=aa:bb:cc")

    assert events.types() == [EventType.DOWNLOAD]
    assert (events.events[0].device_id, events.events[0].to_version) == ("aa:bb:cc", "1.2.0")


def test_a_download_without_a_device_id_still_records(client):
    """A cached or hand-typed URL carries none. The column is nullable so the
    binary still leaves a trace of having been served."""
    firmware = make_firmware(firmware_id=1)
    events = FakeDeviceEventRepository()
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository([firmware])
    app.dependency_overrides[get_storage] = lambda: FakeStorage(
        {firmware.filename: b"binary contents"}
    )
    app.dependency_overrides[get_device_event_repository] = lambda: events

    client.get("/api/download/link-1")

    assert events.types() == [EventType.DOWNLOAD]
    assert events.events[0].device_id is None


def test_a_download_that_404s_records_nothing(client):
    events = FakeDeviceEventRepository()
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository()
    app.dependency_overrides[get_storage] = lambda: FakeStorage()
    app.dependency_overrides[get_device_event_repository] = lambda: events

    assert client.get("/api/download/no-such-link").status_code == 404
    assert events.types() == []
