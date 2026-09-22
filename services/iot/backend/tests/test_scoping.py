"""What one account can reach of another's, driven through the real routes.

Every assertion here is a way the dashboard could leak across tenants, tried
against the wiring an operator actually hits rather than against a repository
in isolation. The repositories refuse this on their own and are tested for it
in `test_sqlite_repo.py`; this is the check that the routes ask them the
scoped question in the first place.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from api.deps import (
    get_device_event_repository,
    get_device_repository,
    get_firmware_repository,
    get_refresh_token_repository,
    get_storage,
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
from domain.models import Device, Firmware, User
from fastapi.testclient import TestClient
from fastapi_users.password import PasswordHelper
from main import app

PASSWORD = "s3cret-password"

_hasher = PasswordHelper()


def make_firmware(owner_id: int, model="ESP32", version="1.0.0", firmware_id=1) -> Firmware:
    return Firmware(
        id=firmware_id,
        owner_id=owner_id,
        download_id=f"link-{firmware_id}",
        model=model,
        version=version,
        filename=f"{firmware_id}.bin",
        original_filename="main.ino.bin",
        signature="sig",
        sha256=str(firmware_id) * 64,
        size_bytes=4,
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def world():
    """Two accounts, each with one firmware and one device."""
    users = FakeUserRepository()
    # Both accounts hold a key, because an account without one cannot publish
    # at all and the uploads below are about ownership, not about signatures.
    # What a signature has to be is `test_upload_firmware.py`.
    alice = users.add(
        User(
            email="alice@example.com",
            hashed_password=_hasher.hash(PASSWORD),
            public_key="a public key",
        )
    )
    bob = users.add(
        User(
            email="bob@example.com",
            hashed_password=_hasher.hash(PASSWORD),
            public_key="a public key",
        )
    )

    firmware = FakeFirmwareRepository(
        [
            make_firmware(alice.id, version="1.0.0", firmware_id=1),
            make_firmware(bob.id, version="2.0.0", firmware_id=2),
        ]
    )
    devices = FakeDeviceRepository()
    devices.register(Device(device_id="alice-dev", model="ESP32", owner_id=alice.id))
    devices.register(Device(device_id="bob-dev", model="ESP32", owner_id=bob.id))
    storage = FakeStorage({"1.bin": b"aaaa", "2.bin": b"bbbb"})

    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_refresh_token_repository] = lambda: FakeRefreshTokenRepository()
    app.dependency_overrides[get_firmware_repository] = lambda: firmware
    app.dependency_overrides[get_device_repository] = lambda: devices
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_device_event_repository] = lambda: FakeDeviceEventRepository()
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


def test_the_firmware_list_holds_only_the_caller_s_own(world, client):
    body = client.get("/api/firmware/list", headers=auth(client, "alice@example.com")).json()

    assert [f["version"] for f in body] == ["1.0.0"]


def test_the_device_list_holds_only_the_caller_s_own(world, client):
    body = client.get("/api/devices", headers=auth(client, "alice@example.com")).json()

    assert [d["device_id"] for d in body] == ["alice-dev"]


def test_the_fleet_counts_stop_at_the_caller_s_own_devices(world, client):
    body = client.get("/api/devices/stats", headers=auth(client, "alice@example.com")).json()

    assert body["total"] == 1


def test_a_new_account_sees_two_empty_lists(world, client):
    """What signing up gets you. Open registration only stops being a hole once
    this is true, which is why the two landed together."""
    client.post("/api/auth/register", json={"email": "carol@example.com", "password": PASSWORD})
    headers = auth(client, "carol@example.com")

    assert client.get("/api/firmware/list", headers=headers).json() == []
    assert client.get("/api/devices", headers=headers).json() == []


def test_withdrawing_someone_else_s_version_is_a_404(world, client):
    """404, not 403. Answering differently would confirm the id exists."""
    res = client.post("/api/firmware/2/deactivate", headers=auth(client, "alice@example.com"))

    assert res.status_code == 404
    assert world["firmware"].get_by_id(2, world["bob"].id).active is True


def test_withdrawing_your_own_version_still_works(world, client):
    res = client.post("/api/firmware/1/deactivate", headers=auth(client, "alice@example.com"))

    assert res.status_code == 200
    assert res.json()["active"] is False


def test_an_upload_lands_under_the_account_that_sent_it(world, client, monkeypatch):
    from application import upload_firmware as use_case_module

    monkeypatch.setattr(use_case_module, "validate_image", lambda data: None)
    monkeypatch.setattr(use_case_module.signing, "verify_manifest", lambda *args, **kwargs: None)

    res = client.post(
        "/firmware/upload",
        files={
            "model": (None, "ESP32"),
            "version": (None, "3.0.0"),
            "signature": (None, "c2lnbmF0dXJl"),
            "firmware": ("f.bin", io.BytesIO(b"\xe9payload"), "application/octet-stream"),
        },
        headers=auth(client, "alice@example.com"),
    )

    assert res.status_code == 200
    published = world["firmware"].added[-1]
    assert published.owner_id == world["alice"].id
    # And it is not suddenly visible to the other account.
    assert [
        f["version"]
        for f in client.get("/api/firmware/list", headers=auth(client, "bob@example.com")).json()
    ] == ["2.0.0"]


def test_a_version_another_account_already_published_is_not_a_conflict(world, client, monkeypatch):
    """Bob holds `ESP32 2.0.0`. Alice publishing her own must not collide."""
    from application import upload_firmware as use_case_module

    monkeypatch.setattr(use_case_module, "validate_image", lambda data: None)
    monkeypatch.setattr(use_case_module.signing, "verify_manifest", lambda *args, **kwargs: None)

    res = client.post(
        "/firmware/upload",
        files={
            "model": (None, "ESP32"),
            "version": (None, "2.0.0"),
            "signature": (None, "c2lnbmF0dXJl"),
            "firmware": ("f.bin", io.BytesIO(b"\xe9payload"), "application/octet-stream"),
        },
        headers=auth(client, "alice@example.com"),
    )

    assert res.status_code == 200


def test_a_download_link_needs_no_account_and_names_no_row(world, client):
    """Unauthenticated by necessity: `ota.cpp` has no credential to send.

    The handle is therefore the whole credential, and it is not the row id, so
    holding one link says nothing about any other.
    """
    res = client.get("/api/download/link-2")

    assert res.status_code == 200
    assert res.content == b"bbbb"
    assert client.get("/api/download/2").status_code == 404


def test_an_id_from_the_firmware_list_is_not_a_download_link(world, client):
    """The list hands out row ids, and those must not open the download route.

    Otherwise scoping the list would be pointless: the ids are sequential, so
    one account could walk the whole range.
    """
    listed = client.get("/api/firmware/list", headers=auth(client, "alice@example.com")).json()

    assert client.get(f"/api/download/{listed[0]['id']}").status_code == 404


def test_the_firmware_list_does_not_hand_out_the_download_link(world, client):
    """Nothing on the dashboard downloads, and the link is a bearer credential.

    Putting it in a list response would spread it to every browser tab, log and
    screenshot that touches the page, for a feature that does not exist.
    """
    body = client.get("/api/firmware/list", headers=auth(client, "alice@example.com")).json()

    assert "download_id" not in body[0]
