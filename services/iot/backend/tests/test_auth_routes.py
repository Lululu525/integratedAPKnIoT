"""HTTP-level tests for the session routes, registration, and the upload route.

Logs in through the real endpoint and uses the returned JWT on the routes
behind it. Accounts are seeded with a real argon2 hash, so login goes through
the same verification a deployed server does rather than a stub that always
agrees.

There is no role gate left to test: any signed-in account may publish, and what
stops it reaching another account's firmware is scoping, which `test_scoping.py`
covers.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from api.deps import (
    get_firmware_repository,
    get_refresh_token_repository,
    get_upload_firmware,
    get_user_repository,
)
from api.routes import MULTIPART_OVERHEAD_ALLOWANCE
from application.upload_firmware import InvalidUploadIdentity
from conftest import FakeFirmwareRepository, FakeRefreshTokenRepository, FakeUserRepository
from domain.firmware_image import MAX_FIRMWARE_BYTES, InvalidFirmwareImage
from domain.models import Firmware, User
from domain.signing import InvalidManifestField
from fastapi.testclient import TestClient
from fastapi_users.password import PasswordHelper
from main import app
from ports.repository import FirmwareAlreadyExists, FirmwareBinaryAlreadyExists

PASSWORD = "s3cret-password"

_hasher = PasswordHelper()


def seed_user(repo: FakeUserRepository, email: str, password: str = PASSWORD) -> User:
    """Add an account the way the database holds one, hash included."""
    return repo.add(User(email=email, hashed_password=_hasher.hash(password)))


def make_firmware(firmware_id=1, owner_id=1) -> Firmware:
    # owner_id 1 is the first account the fake repository hands out, which is
    # the one these tests log in as.
    return Firmware(
        owner_id=owner_id,
        download_id=f"download-{firmware_id}",
        model="ESP32",
        version="1.0.0",
        filename="f.bin",
        original_filename="f.bin",
        signature="s",
        sha256="a" * 64,
        size_bytes=1,
        id=firmware_id,
        created_at=datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


class FakeUploadFirmware:
    def execute(self, req) -> Firmware:
        # The real use case reads these out of the image whenever the form
        # leaves them out, so stand in for that rather than handing None back
        # to a response model that promises strings.
        return Firmware(
            model=req.model or "ESP32",
            version=req.version or "1.0.0",
            filename="f.bin",
            signature="s",
            sha256="a" * 64,
            size_bytes=0,
        )


class RecordingUploadFirmware(FakeUploadFirmware):
    def __init__(self) -> None:
        self.req = None

    def execute(self, req) -> Firmware:
        self.req = req
        return super().execute(req)


class FakeUploadFirmwareTakenVersion:
    def execute(self, req) -> Firmware:
        raise FirmwareAlreadyExists(req.model, req.version)


class FakeUploadFirmwareBadImage:
    def execute(self, req) -> Firmware:
        raise InvalidFirmwareImage("Not an ESP32 image: expected magic 0xE9, found 0x62")


class FakeUploadFirmwareStoredBinary:
    def execute(self, req) -> Firmware:
        raise FirmwareBinaryAlreadyExists(req.model, "1.0.2")


class FakeUploadFirmwareBadVersion:
    def execute(self, req) -> Firmware:
        raise InvalidManifestField("version must look like 1.2.3, got 'v2.0.0'")


class FakeUploadFirmwareContradicted:
    def execute(self, req) -> Firmware:
        raise InvalidUploadIdentity("Image says ESP32 1.0.5, upload says ESP32 1.0.4")


@pytest.fixture
def users():
    repo = FakeUserRepository()
    app.dependency_overrides[get_user_repository] = lambda: repo
    # Overriding the two leaf repositories is enough: the account store
    # fastapi-users reads through and the session use case are both built on
    # top of them by `Depends`, so they pick these up without being named here.
    # One instance for the whole test, not one per request: a refresh handle
    # issued by the login call has to still be there on the call that spends it.
    tokens = FakeRefreshTokenRepository()
    app.dependency_overrides[get_refresh_token_repository] = lambda: tokens
    yield repo
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def login_response(client, email, password=PASSWORD):
    # Form-encoded, not JSON: the login route takes OAuth2's password form, so
    # the identity arrives in a field the standard calls `username`.
    return client.post("/api/auth/login", data={"username": email, "password": password})


def login(client, email, password=PASSWORD) -> str:
    res = login_response(client, email, password)
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def upload_files():
    # The use case is stubbed in this module, so the signature is only here to
    # satisfy the form. What a real one has to be is `test_upload_firmware.py`.
    return {
        "model": (None, "ESP32"),
        "version": (None, "1.0.0"),
        "signature": (None, "c2lnbmF0dXJl"),
        "firmware": ("f.bin", io.BytesIO(b"binary"), "application/octet-stream"),
    }


def test_registration_creates_an_account_that_can_log_in(users, client):
    res = client.post("/api/auth/register", json={"email": "bob@example.com", "password": PASSWORD})

    assert res.status_code == 201
    assert login_response(client, "bob@example.com").status_code == 200


def test_registration_normalizes_the_address(users, client):
    client.post("/api/auth/register", json={"email": "Bob@Example.COM", "password": PASSWORD})

    assert users.get_by_email("bob@example.com") is not None


def test_registration_refuses_a_second_account_on_one_address(users, client):
    """Differing only in case is the same address, so the second one is refused.

    Normalizing on write is what makes this a 400 rather than two accounts
    quietly sharing an inbox with only one of them reachable by login.
    """
    client.post("/api/auth/register", json={"email": "bob@example.com", "password": PASSWORD})

    res = client.post("/api/auth/register", json={"email": "BOB@example.com", "password": PASSWORD})

    assert res.status_code == 400


def test_registration_applies_the_password_rules(users, client):
    res = client.post("/api/auth/register", json={"email": "bob@example.com", "password": "short"})

    assert res.status_code == 400
    assert users.get_by_email("bob@example.com") is None


def test_registration_cannot_hand_itself_the_superuser_flag(users, client):
    """The flag gates nothing today, and must not become grantable by asking.

    `create_update_dict` drops it on this path. A regression here would be
    invisible until the first route that read it, which is the wrong moment to
    discover that anyone could set it.
    """
    client.post(
        "/api/auth/register",
        json={"email": "bob@example.com", "password": PASSWORD, "is_superuser": True},
    )

    assert users.get_by_email("bob@example.com").is_superuser is False


def test_login_rejects_an_unknown_account(users, client):
    res = login_response(client, "nobody@example.com")

    assert res.status_code == 401


def test_login_rejects_overlong_input_as_401(users, client):
    """A clean 401, not a 500 out of the hasher."""
    seed_user(users, "bob@example.com", PASSWORD)

    res = login_response(client, "bob@example.com", "x" * 5000)

    assert res.status_code == 401


def test_login_rejects_bad_password(users, client):
    seed_user(users, "bob@example.com", PASSWORD)

    res = login_response(client, "bob@example.com", "nope")

    assert res.status_code == 401


def test_login_does_not_say_which_half_was_wrong(users, client):
    """One message for both, so the route cannot be used to enumerate accounts."""
    seed_user(users, "bob@example.com", PASSWORD)

    unknown = login_response(client, "nobody@example.com")
    wrong_password = login_response(client, "bob@example.com", "nope")

    assert unknown.json()["detail"] == wrong_password.json()["detail"]


def test_login_finds_the_account_whatever_case_was_typed(users, client):
    seed_user(users, "bob@example.com", PASSWORD)

    res = login_response(client, "BOB@Example.COM")

    assert res.status_code == 200
    assert res.json()["user"]["email"] == "bob@example.com"


def test_login_refuses_a_disabled_account(users, client):
    user = seed_user(users, "bob@example.com", PASSWORD)
    user.is_active = False

    res = login_response(client, "bob@example.com")

    assert res.status_code == 401


def test_login_answers_with_the_account_and_both_tokens(users, client):
    """One round trip answers "who am I" alongside "here is your token".

    Without it the browser would have to decode a credential it is only
    supposed to carry, just to put an address in the sidebar.
    """
    seed_user(users, "bob@example.com", PASSWORD)

    body = login_response(client, "bob@example.com").json()

    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["refresh_token"]
    assert body["user"] == {
        "id": users.get_by_email("bob@example.com").id,
        "email": "bob@example.com",
        # No key yet, which the dashboard reads to say the upload form is
        # unusable before the operator has filled it in.
        "has_public_key": False,
    }


def test_the_access_token_from_login_identifies_the_account(users, client):
    seed_user(users, "bob@example.com", PASSWORD)
    token = login(client, "bob@example.com")

    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    assert res.json()["email"] == "bob@example.com"


def test_me_requires_a_token(users, client):
    res = client.get("/api/auth/me")

    assert res.status_code == 401


def test_refresh_hands_back_a_working_access_token(users, client):
    seed_user(users, "bob@example.com", PASSWORD)
    handle = login_response(client, "bob@example.com").json()["refresh_token"]

    res = client.post("/api/auth/refresh", json={"refresh_token": handle})

    assert res.status_code == 200
    renewed = res.json()
    assert renewed["user"]["email"] == "bob@example.com"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {renewed['access_token']}"})
    assert me.status_code == 200


def test_refresh_needs_no_access_token(users, client):
    """The route exists to be reachable once the access token has expired.

    Gating it behind a live one would make it useful only while it is not
    needed, which is the whole of why it is not behind the bearer dependency.
    """
    seed_user(users, "bob@example.com", PASSWORD)
    handle = login_response(client, "bob@example.com").json()["refresh_token"]

    res = client.post("/api/auth/refresh", json={"refresh_token": handle})

    assert res.status_code == 200


def test_a_used_refresh_handle_is_rejected(users, client):
    seed_user(users, "bob@example.com", PASSWORD)
    handle = login_response(client, "bob@example.com").json()["refresh_token"]
    client.post("/api/auth/refresh", json={"refresh_token": handle})

    res = client.post("/api/auth/refresh", json={"refresh_token": handle})

    assert res.status_code == 401


def test_refresh_rejects_a_handle_that_was_never_issued(users, client):
    res = client.post("/api/auth/refresh", json={"refresh_token": "made-up"})

    assert res.status_code == 401


def test_logout_kills_the_handle_it_is_given(users, client):
    seed_user(users, "bob@example.com", PASSWORD)
    handle = login_response(client, "bob@example.com").json()["refresh_token"]

    logged_out = client.post("/api/auth/logout", json={"refresh_token": handle})

    assert logged_out.status_code == 204
    assert client.post("/api/auth/refresh", json={"refresh_token": handle}).status_code == 401


def test_logout_leaves_another_session_of_the_same_account_alone(users, client):
    seed_user(users, "bob@example.com", PASSWORD)
    laptop = login_response(client, "bob@example.com").json()["refresh_token"]
    phone = login_response(client, "bob@example.com").json()["refresh_token"]

    client.post("/api/auth/logout", json={"refresh_token": laptop})

    assert client.post("/api/auth/refresh", json={"refresh_token": phone}).status_code == 200


def test_forgot_password_says_nothing_about_whether_the_account_exists(users, client):
    """Both answers are 202, or the route becomes an account enumerator."""
    seed_user(users, "bob@example.com", PASSWORD)

    known = client.post("/api/auth/forgot-password", json={"email": "bob@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})

    assert known.status_code == 202
    assert unknown.status_code == 202


def test_reset_password_rejects_a_forged_token(users, client):
    seed_user(users, "bob@example.com", PASSWORD)

    res = client.post(
        "/api/auth/reset-password", json={"token": "not-a-token", "password": "new-password"}
    )

    assert res.status_code == 400


def test_a_reset_lets_the_new_password_in_and_ends_every_session(users, client, caplog):
    """The one flow that has to work end to end, minus the mail transport.

    The token is read out of the log because there is nowhere else for it to
    go, which is exactly what an operator does on this deployment.
    """
    seed_user(users, "bob@example.com", PASSWORD)
    handle = login_response(client, "bob@example.com").json()["refresh_token"]

    with caplog.at_level("WARNING", logger="api.auth"):
        client.post("/api/auth/forgot-password", json={"email": "bob@example.com"})
    token = caplog.text.rsplit(": ", 1)[1].strip()

    reset = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "a-brand-new-password"}
    )

    assert reset.status_code == 200
    assert login_response(client, "bob@example.com", "a-brand-new-password").status_code == 200
    assert login_response(client, "bob@example.com", PASSWORD).status_code == 401
    # The sessions the old password left open die with it. A reset that leaves
    # them live does not end the session the person resetting was locked out of.
    assert client.post("/api/auth/refresh", json={"refresh_token": handle}).status_code == 401


def test_a_reset_refuses_a_password_the_rules_reject(users, client, caplog):
    seed_user(users, "bob@example.com", PASSWORD)
    with caplog.at_level("WARNING", logger="api.auth"):
        client.post("/api/auth/forgot-password", json={"email": "bob@example.com"})
    token = caplog.text.rsplit(": ", 1)[1].strip()

    res = client.post("/api/auth/reset-password", json={"token": token, "password": "short"})

    assert res.status_code == 400
    assert "8 characters" in str(res.json()["detail"])


def test_upload_requires_a_token(users, client):
    res = client.post("/firmware/upload", files=upload_files())

    assert res.status_code == 401


def test_upload_succeeds_for_any_signed_in_account(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_upload_firmware] = lambda: FakeUploadFirmware()
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload", files=upload_files(), headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 200
    # The identity rides back on the response because the uploader need not have
    # typed it: an image carrying a build marker names itself.
    assert res.json() == {"status": "ok", "model": "ESP32", "version": "1.0.0"}


def test_upload_carries_notes_through_to_the_use_case(users, client):
    """The form field name is the whole contract here.

    Normalizing blank notes is the use case's job and tested there. What only
    the route can get wrong is the name Pydantic binds the field under, and a
    typo there silently drops every note the admin types.
    """
    seed_user(users, "admin@example.com", PASSWORD)
    use_case = RecordingUploadFirmware()
    app.dependency_overrides[get_upload_firmware] = lambda: use_case
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload",
        files=upload_files() | {"notes": (None, "Fix SNTP retry storm")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200
    assert use_case.req.notes == "Fix SNTP retry storm"


def test_upload_without_notes_reaches_the_use_case_as_none(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    use_case = RecordingUploadFirmware()
    app.dependency_overrides[get_upload_firmware] = lambda: use_case
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload", files=upload_files(), headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 200
    assert use_case.req.notes is None


def test_upload_conflicts_on_a_version_already_stored(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_upload_firmware] = lambda: FakeUploadFirmwareTakenVersion()
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload", files=upload_files(), headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 409


def test_upload_rejects_a_file_that_is_not_an_esp32_image(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_upload_firmware] = lambda: FakeUploadFirmwareBadImage()
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload", files=upload_files(), headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 400
    # The route must pass the validator's message through, not flatten it.
    assert "0xE9" in res.json()["detail"]


def test_upload_reports_a_label_the_image_contradicts(users, client):
    """Both values reach the admin, since only they can tell which one is wrong."""
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_upload_firmware] = lambda: FakeUploadFirmwareContradicted()
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload", files=upload_files(), headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 400
    assert "1.0.5" in res.json()["detail"]
    assert "1.0.4" in res.json()["detail"]


def test_upload_without_a_typed_model_or_version_is_accepted(users, client):
    """The normal path once an image names itself: the form sends neither field."""
    seed_user(users, "admin@example.com", PASSWORD)
    recorder = RecordingUploadFirmware()
    app.dependency_overrides[get_upload_firmware] = lambda: recorder
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload",
        files={
            "signature": (None, "c2lnbmF0dXJl"),
            "firmware": ("f.bin", io.BytesIO(b"binary"), "application/octet-stream"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200
    assert recorder.req.model is None
    assert recorder.req.version is None


def test_upload_rejects_a_version_the_manifest_cannot_carry(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_upload_firmware] = lambda: FakeUploadFirmwareBadVersion()
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload", files=upload_files(), headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 400
    assert "1.2.3" in res.json()["detail"]


def test_upload_conflicts_on_a_binary_already_stored(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_upload_firmware] = lambda: FakeUploadFirmwareStoredBinary()
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload", files=upload_files(), headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 409
    assert "1.0.2" in res.json()["detail"]


def test_deactivate_requires_a_token(client):
    res = client.post("/api/firmware/1/deactivate")

    assert res.status_code == 401


def test_deactivate_clears_active(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository(
        [make_firmware()]
    )
    token = login(client, "admin@example.com")

    res = client.post("/api/firmware/1/deactivate", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    assert res.json()["active"] is False


def test_deactivate_is_idempotent(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository(
        [make_firmware()]
    )
    token = login(client, "admin@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/api/firmware/1/deactivate", headers=headers)
    second = client.post("/api/firmware/1/deactivate", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["active"] is False


def test_deactivate_returns_404_for_unknown_id(users, client):
    seed_user(users, "admin@example.com", PASSWORD)
    app.dependency_overrides[get_firmware_repository] = lambda: FakeFirmwareRepository()
    token = login(client, "admin@example.com")

    res = client.post("/api/firmware/999/deactivate", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 404


def test_upload_rejects_a_body_past_the_ceiling(users, client):
    """413, not 400. Too small means "not an image"; too large means "too large"."""
    seed_user(users, "admin@example.com", PASSWORD)
    use_case = RecordingUploadFirmware()
    app.dependency_overrides[get_upload_firmware] = lambda: use_case
    token = login(client, "admin@example.com")

    oversized = upload_files() | {
        "firmware": (
            "f.bin",
            io.BytesIO(b"\xe9" * (MAX_FIRMWARE_BYTES + 1)),
            "application/octet-stream",
        )
    }
    res = client.post(
        "/firmware/upload", files=oversized, headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 413
    # Nothing was signed, stored or written: the use case never ran.
    assert use_case.req is None


def test_upload_accepts_a_body_at_the_ceiling(users, client):
    """An off-by-one here rejects a legitimate build with no way to tell why."""
    seed_user(users, "admin@example.com", PASSWORD)
    use_case = RecordingUploadFirmware()
    app.dependency_overrides[get_upload_firmware] = lambda: use_case
    token = login(client, "admin@example.com")

    at_limit = upload_files() | {
        "firmware": (
            "f.bin",
            io.BytesIO(b"\xe9" * MAX_FIRMWARE_BYTES),
            "application/octet-stream",
        )
    }
    res = client.post(
        "/firmware/upload", files=at_limit, headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 200
    assert len(use_case.req.data) == MAX_FIRMWARE_BYTES


def test_upload_rejects_an_oversized_declared_body_before_reading_it(users, client):
    """The header check is the only one that can answer without reading the file."""
    seed_user(users, "admin@example.com", PASSWORD)
    use_case = RecordingUploadFirmware()
    app.dependency_overrides[get_upload_firmware] = lambda: use_case
    token = login(client, "admin@example.com")

    res = client.post(
        "/firmware/upload",
        files=upload_files(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Length": str(MAX_FIRMWARE_BYTES + MULTIPART_OVERHEAD_ALLOWANCE + 1),
        },
    )

    assert res.status_code == 413
    assert use_case.req is None


def test_upload_that_understates_its_length_is_still_capped(users, client):
    """The declared length is written by the client, so it cannot be the rule.

    The allowance the header check carries for multipart overhead makes this
    reachable in the other direction too: a body inside that slack passes the
    fast path and is stopped by the read.
    """
    seed_user(users, "admin@example.com", PASSWORD)
    use_case = RecordingUploadFirmware()
    app.dependency_overrides[get_upload_firmware] = lambda: use_case
    token = login(client, "admin@example.com")

    oversized = upload_files() | {
        "firmware": (
            "f.bin",
            io.BytesIO(b"\xe9" * (MAX_FIRMWARE_BYTES + 1)),
            "application/octet-stream",
        )
    }
    res = client.post(
        "/firmware/upload",
        files=oversized,
        headers={"Authorization": f"Bearer {token}", "Content-Length": "10"},
    )

    assert res.status_code == 413
    assert use_case.req is None
