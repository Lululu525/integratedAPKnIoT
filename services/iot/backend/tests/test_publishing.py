"""Publishing end to end with real keys, through the real routes.

Nothing is stubbed here: the account sets a public key over HTTP, a signature
is produced with the matching private key the way `scripts/sign_firmware.py`
does, and the upload route verifies it. This is the one place the whole
verify-then-store path runs as deployed.
"""

from __future__ import annotations

import io
import struct

import pytest
from api.deps import (
    get_firmware_repository,
    get_refresh_token_repository,
    get_storage,
    get_user_repository,
)
from conftest import (
    FakeFirmwareRepository,
    FakeRefreshTokenRepository,
    FakeStorage,
    FakeUserRepository,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from domain import signing
from domain.firmware_image import (
    APP_DESC_MAGIC,
    APP_DESC_OFFSET,
    CHIP_ID_OFFSET,
    IMAGE_MAGIC,
    MIN_FIRMWARE_BYTES,
)
from domain.models import User
from fastapi.testclient import TestClient
from fastapi_users.password import PasswordHelper
from main import app

PASSWORD = "s3cret-password"

_hasher = PasswordHelper()


def valid_image(payload: int = 0) -> bytes:
    """Minimal bytes that clear `validate_image`, varying only after the header.

    `payload` changes the tail rather than the whole buffer, so two images
    differ in nothing but their hash. Filling the header too would set the
    hash_appended flag and make the image claim a trailing digest it does not
    carry.
    """
    image = bytearray(MIN_FIRMWARE_BYTES)
    image[0] = IMAGE_MAGIC
    struct.pack_into("<H", image, CHIP_ID_OFFSET, 0x0009)
    struct.pack_into("<I", image, APP_DESC_OFFSET, APP_DESC_MAGIC)
    image[APP_DESC_OFFSET + 4 :] = bytes([payload]) * (MIN_FIRMWARE_BYTES - APP_DESC_OFFSET - 4)
    return bytes(image)


def make_keypair() -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture(scope="module")
def alice_keys():
    return make_keypair()


@pytest.fixture
def world():
    users = FakeUserRepository()
    users.add(User(email="alice@example.com", hashed_password=_hasher.hash(PASSWORD)))
    users.add(User(email="bob@example.com", hashed_password=_hasher.hash(PASSWORD)))
    firmware = FakeFirmwareRepository()
    storage = FakeStorage()

    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_refresh_token_repository] = lambda: FakeRefreshTokenRepository()
    app.dependency_overrides[get_firmware_repository] = lambda: firmware
    app.dependency_overrides[get_storage] = lambda: storage
    yield {"users": users, "firmware": firmware, "storage": storage}
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def auth(client, email) -> dict[str, str]:
    res = client.post("/api/auth/login", data={"username": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def set_public_key(client, email, public_pem) -> None:
    res = client.put(
        "/api/auth/public-key", json={"public_key": public_pem}, headers=auth(client, email)
    )
    assert res.status_code == 200, res.text


def sign(private_pem, data, model="ESP32", version="1.0.0") -> str:
    """What `scripts/sign_firmware.py` prints, computed the same way."""
    return signing.sign_manifest(model, version, signing.calculate_sha256_bytes(data), private_pem)


def publish(client, email, data, signature, model="ESP32", version="1.0.0"):
    return client.post(
        "/firmware/upload",
        files={
            "model": (None, model),
            "version": (None, version),
            "signature": (None, signature),
            "firmware": ("main.ino.bin", io.BytesIO(data), "application/octet-stream"),
        },
        headers=auth(client, email),
    )


def test_a_new_account_has_no_key_and_cannot_publish(world, client, alice_keys):
    private_pem, _ = alice_keys
    data = valid_image()

    res = publish(client, "alice@example.com", data, sign(private_pem, data))

    assert res.status_code == 400
    assert "public key" in res.json()["detail"]
    assert world["storage"].files == {}


def test_me_reports_whether_a_key_is_set(world, client, alice_keys):
    _, public_pem = alice_keys
    headers = auth(client, "alice@example.com")
    assert client.get("/api/auth/me", headers=headers).json()["has_public_key"] is False

    set_public_key(client, "alice@example.com", public_pem)

    assert client.get("/api/auth/me", headers=headers).json()["has_public_key"] is True


def test_setting_a_key_then_publishing_with_it_works(world, client, alice_keys):
    private_pem, public_pem = alice_keys
    set_public_key(client, "alice@example.com", public_pem)
    data = valid_image()
    signature = sign(private_pem, data)

    res = publish(client, "alice@example.com", data, signature)

    assert res.status_code == 200, res.text
    stored = world["firmware"].added[0]
    # Stored verbatim, compared against the exact string that was sent rather
    # than a freshly computed one: PSS salts randomly, so signing twice gives
    # two different valid signatures. What matters is that the row holds the
    # one the device will be handed.
    assert stored.signature == signature
    assert stored.sha256 == signing.calculate_sha256_bytes(data)


def test_a_signature_from_another_account_s_key_is_refused(world, client, alice_keys):
    """Firmware signed with A's key, uploaded under B. One of #75's Done-whens."""
    alice_private, alice_public = alice_keys
    bob_private, bob_public = make_keypair()
    set_public_key(client, "alice@example.com", alice_public)
    set_public_key(client, "bob@example.com", bob_public)
    data = valid_image()

    res = publish(client, "bob@example.com", data, sign(alice_private, data))

    assert res.status_code == 400
    assert "signature" in res.json()["detail"]
    assert world["storage"].files == {}
    # And Alice, holding the matching key, publishes the same bytes fine.
    assert publish(client, "alice@example.com", data, sign(alice_private, data)).status_code == 200


def test_a_signature_for_a_different_build_is_refused(world, client, alice_keys):
    private_pem, public_pem = alice_keys
    set_public_key(client, "alice@example.com", public_pem)

    res = publish(client, "alice@example.com", valid_image(2), sign(private_pem, valid_image(1)))

    assert res.status_code == 400
    assert world["storage"].files == {}


def test_a_signature_for_a_different_version_is_refused(world, client, alice_keys):
    """The manifest names the version, so republishing one signature is not on."""
    private_pem, public_pem = alice_keys
    set_public_key(client, "alice@example.com", public_pem)
    data = valid_image()

    res = publish(
        client, "alice@example.com", data, sign(private_pem, data, version="1.0.0"), version="1.0.1"
    )

    assert res.status_code == 400


def test_the_public_key_route_refuses_something_that_is_not_a_key(world, client):
    res = client.put(
        "/api/auth/public-key",
        json={"public_key": "-----BEGIN PUBLIC KEY-----\nnope\n-----END PUBLIC KEY-----"},
        headers=auth(client, "alice@example.com"),
    )

    assert res.status_code == 400


def test_the_public_key_route_refuses_a_private_key(world, client, alice_keys):
    """Pasting the wrong half of the pair is the mistake worth catching here.

    Accepting it would store the private key on the server, which is precisely
    the thing this whole change removes.
    """
    private_pem, _ = alice_keys

    res = client.put(
        "/api/auth/public-key",
        json={"public_key": private_pem.decode()},
        headers=auth(client, "alice@example.com"),
    )

    assert res.status_code == 400
    assert world["users"].get_by_email("alice@example.com").public_key is None


def test_the_public_key_route_refuses_a_short_key(world, client):
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pem = (
        weak.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    res = client.put(
        "/api/auth/public-key", json={"public_key": pem}, headers=auth(client, "alice@example.com")
    )

    assert res.status_code == 400


def test_setting_a_key_needs_an_account(world, client, alice_keys):
    _, public_pem = alice_keys

    res = client.put("/api/auth/public-key", json={"public_key": public_pem})

    assert res.status_code == 401


def test_replacing_a_key_changes_what_new_uploads_verify_against(world, client, alice_keys):
    """Rotation is allowed and does not reach backwards.

    Rows already published keep the signature they were stored with, and the
    fleet still verifies those against the copy in its own config.json, so
    rotating here without reflashing changes nothing for anything in the field.
    """
    old_private, old_public = alice_keys
    new_private, new_public = make_keypair()
    set_public_key(client, "alice@example.com", old_public)
    first = valid_image(1)
    first_signature = sign(old_private, first)
    assert publish(client, "alice@example.com", first, first_signature).status_code == 200

    set_public_key(client, "alice@example.com", new_public)
    second = valid_image(2)

    assert (
        publish(
            client,
            "alice@example.com",
            second,
            sign(old_private, second, version="1.0.1"),
            version="1.0.1",
        ).status_code
        == 400
    )
    assert (
        publish(
            client,
            "alice@example.com",
            second,
            sign(new_private, second, version="1.0.1"),
            version="1.0.1",
        ).status_code
        == 200
    )
    # The row published under the old key is untouched.
    assert world["firmware"].added[0].signature == first_signature
