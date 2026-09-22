"""Registering a unit, and what its secret is and is not good for.

Driven through the routes, because the properties worth holding down here are
about what an unauthenticated caller can get out of `/api/check`, which is the
one endpoint on this server anyone on the network can reach.
"""

from __future__ import annotations

import io

import pytest
from api.deps import (
    get_device_event_repository,
    get_device_repository,
    get_firmware_repository,
    get_refresh_token_repository,
    get_storage,
    get_upload_firmware,
    get_user_repository,
)
from conftest import (
    FakeDeviceEventRepository,
    FakeDeviceRepository,
    FakeFirmwareRepository,
    FakeRefreshTokenRepository,
    FakeStorage,
    FakeUserRepository,
)
from domain.models import Firmware, User
from fastapi.testclient import TestClient
from fastapi_users.password import PasswordHelper
from main import app

PASSWORD = "s3cret-password"

_hasher = PasswordHelper()


def make_firmware(owner_id: int, version: str, firmware_id: int) -> Firmware:
    return Firmware(
        id=firmware_id,
        owner_id=owner_id,
        download_id=f"link-{firmware_id}",
        model="ESP32",
        version=version,
        filename=f"{firmware_id}.bin",
        signature=f"sig-{firmware_id}",
        sha256=str(firmware_id) * 64,
        size_bytes=4,
    )


class StubUploadFirmware:
    def execute(self, req):
        return Firmware(
            owner_id=req.owner_id,
            model=req.model or "ESP32",
            version=req.version or "1.0.0",
            filename="f.bin",
            signature="s",
            sha256="a" * 64,
            size_bytes=0,
        )


@pytest.fixture
def world():
    users = FakeUserRepository()
    alice = users.add(User(email="alice@example.com", hashed_password=_hasher.hash(PASSWORD)))
    bob = users.add(User(email="bob@example.com", hashed_password=_hasher.hash(PASSWORD)))

    # Both accounts publish under one model name, which is the situation
    # ownership creates and the secret is what resolves.
    firmware = FakeFirmwareRepository(
        [
            make_firmware(alice.id, "1.1.0", 1),
            make_firmware(bob.id, "9.9.9", 2),
        ]
    )
    devices = FakeDeviceRepository()

    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_refresh_token_repository] = lambda: FakeRefreshTokenRepository()
    app.dependency_overrides[get_firmware_repository] = lambda: firmware
    app.dependency_overrides[get_device_repository] = lambda: devices
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()
    app.dependency_overrides[get_storage] = lambda: FakeStorage(
        {"1.bin": b"aaaa", "2.bin": b"bbbb"}
    )
    app.dependency_overrides[get_upload_firmware] = lambda: StubUploadFirmware()
    yield {"alice": alice, "bob": bob, "firmware": firmware, "devices": devices}
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def auth(client, email) -> dict[str, str]:
    res = client.post("/api/auth/login", data={"username": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def register(client, email, model="ESP32") -> dict:
    res = client.post("/api/devices", json={"model": model}, headers=auth(client, email))
    assert res.status_code == 201, res.text
    return res.json()


def check(client, unit, version="1.0.0", **overrides):
    return client.post(
        "/api/check",
        json={
            "model": "ESP32",
            "version": version,
            "device_id": unit["device_id"],
            "device_secret": unit["device_secret"],
            "poll_interval_seconds": 6,
            "rssi": -52,
            "ip": "10.0.4.11",
        }
        | overrides,
    )


def test_registering_answers_with_an_id_and_a_secret(world, client):
    unit = register(client, "alice@example.com")

    assert unit["model"] == "ESP32"
    assert unit["device_id"]
    assert unit["device_secret"]
    assert unit["device_id"] != unit["device_secret"]


def test_two_registrations_share_nothing(world, client):
    """Per device, not per tenant. One shared secret would mean buying one cheap
    sensor and dumping its flash yields the firmware for everything that tenant
    ships, including the expensive gateway."""
    first = register(client, "alice@example.com")
    second = register(client, "alice@example.com")

    assert first["device_id"] != second["device_id"]
    assert first["device_secret"] != second["device_secret"]


def test_the_secret_is_never_shown_again(world, client):
    unit = register(client, "alice@example.com")

    listed = client.get("/api/devices", headers=auth(client, "alice@example.com")).json()

    assert [d["device_id"] for d in listed] == [unit["device_id"]]
    assert "device_secret" not in listed[0]
    assert "secret_hash" not in listed[0]


def test_registering_needs_an_account(world, client):
    res = client.post("/api/devices", json={"model": "ESP32"})

    assert res.status_code == 401


def test_a_registered_device_is_served_its_own_account_s_firmware(world, client):
    unit = register(client, "alice@example.com")

    body = check(client, unit).json()

    # Alice's 1.1.0, not Bob's 9.9.9, though both are published as ESP32.
    assert body["update_available"] is True
    assert body["version"] == "1.1.0"


def test_two_tenants_under_one_model_name_each_serve_their_own(world, client):
    alice_unit = register(client, "alice@example.com")
    bob_unit = register(client, "bob@example.com")

    assert check(client, alice_unit).json()["version"] == "1.1.0"
    assert check(client, bob_unit).json()["version"] == "9.9.9"


def test_an_unregistered_device_gets_401_and_leaves_no_record(world, client):
    res = check(client, {"device_id": "invented", "device_secret": "invented"})

    assert res.status_code == 401
    assert world["devices"].get_by_device_id("invented") is None


def test_a_wrong_secret_gets_401(world, client):
    unit = register(client, "alice@example.com")

    res = check(client, {"device_id": unit["device_id"], "device_secret": "not-it"})

    assert res.status_code == 401


def test_one_unit_s_secret_does_not_work_for_another(world, client):
    first = register(client, "alice@example.com")
    second = register(client, "alice@example.com")

    res = check(client, {"device_id": first["device_id"], "device_secret": second["device_secret"]})

    assert res.status_code == 401


def test_disabling_a_device_stops_its_next_check_in(world, client):
    unit = register(client, "alice@example.com")
    assert check(client, unit).status_code == 200

    disabled = client.post(
        f"/api/devices/{unit['device_id']}/disable", headers=auth(client, "alice@example.com")
    )

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert check(client, unit).status_code == 401


def test_disabling_one_device_leaves_the_others_working(world, client):
    doomed = register(client, "alice@example.com")
    survivor = register(client, "alice@example.com")

    client.post(
        f"/api/devices/{doomed['device_id']}/disable", headers=auth(client, "alice@example.com")
    )

    assert check(client, doomed).status_code == 401
    assert check(client, survivor).status_code == 200


def test_a_disabled_device_can_be_switched_back_on(world, client):
    unit = register(client, "alice@example.com")
    headers = auth(client, "alice@example.com")
    client.post(f"/api/devices/{unit['device_id']}/disable", headers=headers)

    client.post(f"/api/devices/{unit['device_id']}/enable", headers=headers)

    assert check(client, unit).status_code == 200


def test_another_account_cannot_disable_your_device(world, client):
    unit = register(client, "alice@example.com")

    res = client.post(
        f"/api/devices/{unit['device_id']}/disable", headers=auth(client, "bob@example.com")
    )

    assert res.status_code == 404
    assert check(client, unit).status_code == 200


def test_a_device_secret_cannot_publish_firmware(world, client):
    """It identifies, it never authorizes.

    The secret sits in LittleFS in the clear, so a teardown yields it. The
    moment it is accepted for anything that changes state, that teardown turns
    from a read into a write.
    """
    unit = register(client, "alice@example.com")

    res = client.post(
        "/firmware/upload",
        files={"firmware": ("f.bin", io.BytesIO(b"binary"), "application/octet-stream")},
        headers={"Authorization": f"Bearer {unit['device_secret']}"},
    )

    assert res.status_code == 401


def test_a_device_secret_cannot_withdraw_firmware(world, client):
    unit = register(client, "alice@example.com")

    res = client.post(
        "/api/firmware/1/deactivate",
        headers={"Authorization": f"Bearer {unit['device_secret']}"},
    )

    assert res.status_code == 401


def test_a_device_secret_cannot_read_the_dashboard(world, client):
    unit = register(client, "alice@example.com")
    headers = {"Authorization": f"Bearer {unit['device_secret']}"}

    assert client.get("/api/firmware/list", headers=headers).status_code == 401
    assert client.get("/api/devices", headers=headers).status_code == 401


def test_a_check_in_without_a_secret_is_a_422(world, client):
    """A 422 rather than a 401: the body is not a check-in at all.

    Devices flashed before registration send this shape, and the missing field
    is what says so.
    """
    unit = register(client, "alice@example.com")

    res = client.post(
        "/api/check",
        json={
            "model": "ESP32",
            "version": "1.0.0",
            "device_id": unit["device_id"],
            "poll_interval_seconds": 6,
            "rssi": -52,
            "ip": "10.0.4.11",
        },
    )

    assert res.status_code == 422


def test_a_check_in_appears_on_its_owner_s_dashboard(world, client):
    unit = register(client, "alice@example.com")
    check(client, unit, version="1.0.0")

    listed = client.get("/api/devices", headers=auth(client, "alice@example.com")).json()

    assert listed[0]["current_version"] == "1.0.0"
    assert client.get("/api/devices", headers=auth(client, "bob@example.com")).json() == []


def test_a_teardown_does_not_yield_another_model_s_firmware(world, client):
    """The model steers the lookup, so it may not come from the request body.

    This is what buying one unit is meant to be worth. A cheap sensor's secret
    reads that sensor's updates; claiming to be the expensive gateway has to
    get the caller nothing, or the per-device secret buys nothing over a
    per-tenant one.
    """
    world["firmware"].add(
        Firmware(
            owner_id=world["alice"].id,
            download_id="gateway-link",
            model="GATEWAY",
            version="4.0.0",
            filename="gw.bin",
            signature="gateway-signature",
            sha256="c" * 64,
            size_bytes=4,
        )
    )
    sensor = register(client, "alice@example.com", model="ESP32")

    res = check(client, sensor, model="GATEWAY")

    assert res.status_code == 403, res.text
    assert "gateway-signature" not in res.text


def test_a_check_in_cannot_rewrite_its_own_model(world, client):
    """The row's model is the owner's statement about the unit, not the unit's.

    Left writable, a teardown relabels the row and the next poll is served the
    other model's firmware anyway, which is the same hole one check-in later.
    """
    unit = register(client, "alice@example.com", model="ESP32")

    check(client, unit, model="GATEWAY")

    stored = world["devices"].get_by_device_id(unit["device_id"])
    assert stored.model == "ESP32"
