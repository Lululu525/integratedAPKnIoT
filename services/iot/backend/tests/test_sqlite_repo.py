from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from domain.models import (
    Device,
    DeviceEvent,
    EventType,
    Firmware,
    RefreshToken,
    User,
    hash_device_secret,
)
from infrastructure.db import Base
from infrastructure.sqlite_repo import (
    SqliteDeviceEventRepository,
    SqliteDeviceRepository,
    SqliteFirmwareRepository,
    SqliteRefreshTokenRepository,
    SqliteUserRepository,
)
from ports.repository import (
    DeviceAlreadyExists,
    FirmwareAlreadyExists,
    FirmwareBinaryAlreadyExists,
    UserAlreadyExists,
    UserNotFound,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def session():
    # A single shared in-memory connection: plain `sqlite://` would hand each
    # connection its own throwaway database, so `StaticPool` keeps every use
    # of this engine on the same connection.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# Two accounts, so every scoped read can be asked the question it exists to
# answer: what the other one sees.
OWNER = 1
OTHER_OWNER = 2


def make_firmware(model="ESP32", version="1.0.0", sha256=None, owner_id=OWNER) -> Firmware:
    # Distinct bytes per version by default, since (owner, model, sha256) is a
    # unique index too. A fixed hash would make every two-version test a
    # collision on an axis it is not about.
    digest = sha256 or hashlib.sha256(version.encode()).hexdigest()
    return Firmware(
        owner_id=owner_id,
        # Unique per row without being random, so a test mixing owners can
        # still name the link it means.
        download_id=f"dl-{owner_id}-{model}-{version}-{digest[:8]}",
        model=model,
        version=version,
        filename=f"{version}.bin",
        signature="sig",
        sha256=digest,
        size_bytes=1,
    )


def test_add_assigns_id_and_persists_fields(session):
    repo = SqliteFirmwareRepository(session)

    added = repo.add(make_firmware())

    assert added.id is not None
    fetched = repo.get_by_id(added.id, OWNER)
    assert fetched == added
    assert fetched.active is True


def test_get_by_id_returns_none_when_missing(session):
    repo = SqliteFirmwareRepository(session)

    assert repo.get_by_id(999, OWNER) is None


def test_get_latest_for_model_picks_highest_dotted_version(session):
    repo = SqliteFirmwareRepository(session)
    # Out of insertion order, and "1.2.9" would sort after "1.2.10" lexically.
    for version in ["1.0.0", "1.2.10", "1.2.9", "1.2.2"]:
        repo.add(make_firmware(version=version))

    latest = repo.get_latest_for_model("ESP32", OWNER)

    assert latest.version == "1.2.10"


def test_get_latest_for_model_breaks_version_tie_by_newest_row(session):
    repo = SqliteFirmwareRepository(session)
    # Distinct versions can still parse to the same tuple: the parser reads at
    # most three segments and stops at the first non-digit. So the tie-break
    # picks the later upload rather than depending on the query's row order.
    first = repo.add(make_firmware(version="1.2.3"))
    second = repo.add(make_firmware(version="1.2.3.4"))

    latest = repo.get_latest_for_model("ESP32", OWNER)

    assert latest.id == second.id
    assert second.id > first.id


def test_add_rejects_a_version_already_stored_for_the_model(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(version="1.2.0"))

    # Different bytes, so only the version axis can be what rejects this.
    with pytest.raises(FirmwareAlreadyExists):
        repo.add(make_firmware(version="1.2.0", sha256="f" * 64))


def test_add_rejects_a_binary_already_stored_for_the_model(session):
    """The race `get_by_sha256` cannot close.

    Two uploads of one binary both read `None` from that pre-check and both
    proceed, so this goes straight at `add` to exercise what the index does.
    Same bytes under a different version is the only shape the race produces:
    equal versions would be rejected by the older constraint instead.
    """
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(version="1.2.0", sha256="b" * 64))

    with pytest.raises(FirmwareBinaryAlreadyExists) as exc_info:
        repo.add(make_firmware(version="1.3.0", sha256="b" * 64))

    # Names the version already serving those bytes, which is what the 409 says.
    assert exc_info.value.existing_version == "1.2.0"


def test_add_allows_the_same_binary_on_another_model(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(model="ESP32", sha256="b" * 64))

    added = repo.add(make_firmware(model="ESP32-S3", sha256="b" * 64))

    assert added.id is not None


def test_add_allows_the_same_version_on_another_model(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(model="ESP32", version="1.2.0"))

    added = repo.add(make_firmware(model="ESP32-S3", version="1.2.0"))

    assert added.id is not None


def test_get_by_sha256_finds_a_binary_already_stored(session):
    repo = SqliteFirmwareRepository(session)
    added = repo.add(make_firmware(version="1.0.2", sha256="b" * 64))

    found = repo.get_by_sha256("ESP32", "b" * 64, OWNER)

    assert found.id == added.id
    assert found.version == "1.0.2"


def test_get_by_sha256_returns_none_when_no_binary_matches(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(sha256="b" * 64))

    assert repo.get_by_sha256("ESP32", "c" * 64, OWNER) is None


def test_get_by_sha256_ignores_the_same_binary_on_another_model(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(model="ESP32", sha256="b" * 64))

    assert repo.get_by_sha256("ESP32-S3", "b" * 64, OWNER) is None


def test_get_latest_for_model_ignores_other_models(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(model="ESP32", version="1.0.0"))
    repo.add(make_firmware(model="ESP32-S3", version="9.9.9"))

    latest = repo.get_latest_for_model("ESP32", OWNER)

    assert latest.version == "1.0.0"


def test_get_latest_for_model_returns_none_when_no_firmware(session):
    repo = SqliteFirmwareRepository(session)

    assert repo.get_latest_for_model("ESP32", OWNER) is None


def test_list_all_orders_newest_first(session):
    repo = SqliteFirmwareRepository(session)
    first = repo.add(make_firmware(version="1.0.0"))
    second = repo.add(make_firmware(version="1.1.0"))

    listed = repo.list_all(OWNER)

    assert [f.id for f in listed] == [second.id, first.id]


def test_get_latest_for_model_skips_inactive_row(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(version="1.0.0"))
    newest = repo.add(make_firmware(version="1.1.0"))

    repo.deactivate(newest.id, OWNER)

    latest = repo.get_latest_for_model("ESP32", OWNER)
    assert latest is not None
    assert latest.version == "1.0.0"


def test_get_latest_for_model_returns_none_when_every_row_inactive(session):
    repo = SqliteFirmwareRepository(session)
    first = repo.add(make_firmware(version="1.0.0"))
    second = repo.add(make_firmware(version="1.1.0"))
    repo.deactivate(first.id, OWNER)
    repo.deactivate(second.id, OWNER)

    assert repo.get_latest_for_model("ESP32", OWNER) is None


def test_list_all_still_returns_inactive_rows(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(version="1.0.0"))
    second = repo.add(make_firmware(version="1.1.0"))

    repo.deactivate(second.id, OWNER)

    listed = repo.list_all(OWNER)

    assert len(listed) == 2
    assert {f.active for f in listed} == {True, False}


def test_deactivate_returns_updated_row_and_is_idempotent(session):
    repo = SqliteFirmwareRepository(session)
    added = repo.add(make_firmware())

    first = repo.deactivate(added.id, OWNER)
    second = repo.deactivate(added.id, OWNER)

    assert first is not None
    assert first.id == added.id
    assert first.active is False
    assert second is not None
    assert second.id == added.id
    assert second.active is False


def test_deactivate_returns_none_for_unknown_id(session):
    repo = SqliteFirmwareRepository(session)

    assert repo.deactivate(999, OWNER) is None


def test_one_owners_firmware_is_invisible_to_another(session):
    repo = SqliteFirmwareRepository(session)
    mine = repo.add(make_firmware(version="1.0.0"))

    assert repo.get_by_id(mine.id, OTHER_OWNER) is None
    assert repo.list_all(OTHER_OWNER) == []
    assert repo.get_latest_for_model("ESP32", OTHER_OWNER) is None
    assert repo.get_by_sha256("ESP32", mine.sha256, OTHER_OWNER) is None


def test_another_owner_cannot_withdraw_a_version(session):
    repo = SqliteFirmwareRepository(session)
    mine = repo.add(make_firmware())

    assert repo.deactivate(mine.id, OTHER_OWNER) is None
    assert repo.get_by_id(mine.id, OWNER).active is True


def test_two_owners_can_each_publish_the_same_model_and_version(session):
    """The widened index is what makes a model name a per-tenant label.

    Scoped to (model, version) alone, the first tenant to publish `ESP32 1.0.0`
    would stop every other tenant from ever publishing their own.
    """
    repo = SqliteFirmwareRepository(session)

    mine = repo.add(make_firmware(version="1.0.0"))
    theirs = repo.add(make_firmware(version="1.0.0", owner_id=OTHER_OWNER))

    assert mine.id != theirs.id
    assert repo.get_latest_for_model("ESP32", OWNER).id == mine.id
    assert repo.get_latest_for_model("ESP32", OTHER_OWNER).id == theirs.id


def test_two_owners_can_upload_identical_bytes(session):
    """One blob backs both rows, which is what content addressing already meant.

    It now crosses a trust boundary, so nothing may delete the file because one
    owner's row went away.
    """
    repo = SqliteFirmwareRepository(session)
    shared = "c" * 64

    mine = repo.add(make_firmware(version="1.0.0", sha256=shared))
    theirs = repo.add(make_firmware(version="1.0.0", sha256=shared, owner_id=OTHER_OWNER))

    assert mine.filename == theirs.filename


def test_one_owner_still_cannot_store_one_binary_twice(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware(version="1.0.0", sha256="d" * 64))

    with pytest.raises(FirmwareBinaryAlreadyExists):
        repo.add(make_firmware(version="1.0.1", sha256="d" * 64))


def test_a_download_id_finds_its_row_without_an_owner(session):
    """The link is the credential, so this lookup is unscoped on purpose.

    `ota.cpp` has no account to authenticate as, so the only thing between a
    caller and the bytes is holding the identifier.
    """
    repo = SqliteFirmwareRepository(session)
    stored = repo.add(make_firmware())

    assert repo.get_by_download_id(stored.download_id).id == stored.id


def test_an_unknown_download_id_finds_nothing(session):
    repo = SqliteFirmwareRepository(session)
    repo.add(make_firmware())

    assert repo.get_by_download_id("not-a-link") is None


def register(repo, device_id="dev-1", owner_id=OWNER, secret="s3cret", **overrides) -> Device:
    return repo.register(
        Device(
            device_id=device_id,
            model="ESP32",
            owner_id=owner_id,
            secret_hash=hash_device_secret(secret),
            **overrides,
        )
    )


def test_devices_are_listed_only_for_their_owner(session):
    repo = SqliteDeviceRepository(session)
    register(repo, "mine", OWNER)
    register(repo, "theirs", OTHER_OWNER)

    assert [d.device_id for d in repo.list_all(OWNER)] == ["mine"]
    assert [d.device_id for d in repo.list_all(OTHER_OWNER)] == ["theirs"]


def test_registering_stamps_the_moment_and_starts_enabled(session):
    repo = SqliteDeviceRepository(session)

    device = register(repo)

    assert device.id is not None
    assert device.enabled is True
    assert device.registered_at is not None
    assert device.registered_at.tzinfo is not None


def test_registering_one_device_id_twice_is_refused(session):
    repo = SqliteDeviceRepository(session)
    register(repo)

    with pytest.raises(DeviceAlreadyExists):
        register(repo)


def test_a_check_in_for_a_device_with_no_row_writes_nothing(session):
    """Never inserts, which is the whole change registration makes here.

    Before it, this method created a row for any identifier it had not seen,
    populated entirely from the request, so any string could become a device.
    """
    repo = SqliteDeviceRepository(session)

    recorded = repo.record_checkin(Device(device_id="stranger", model="ESP32"))

    assert recorded is None
    assert repo.get_by_device_id("stranger") is None


def test_a_check_in_writes_what_the_device_reported(session):
    repo = SqliteDeviceRepository(session)
    registered = register(repo)

    updated = repo.record_checkin(
        Device(device_id="dev-1", model="ESP32", current_version="1.1.0", rssi=-52)
    )

    assert updated.id == registered.id
    assert updated.current_version == "1.1.0"
    assert updated.rssi == -52


def test_a_check_in_cannot_rewrite_owner_secret_or_enabled(session):
    """Three columns a check-in may not touch, and one test that says why.

    A device that could write any of them could hand itself to another account,
    replace the credential it is checked against, or switch itself back on
    after being disabled.
    """
    repo = SqliteDeviceRepository(session)
    register(repo, secret="the-real-secret")
    original = repo.get_by_device_id("dev-1")
    repo.set_enabled("dev-1", OWNER, False)

    repo.record_checkin(
        Device(
            device_id="dev-1",
            model="ESP32",
            owner_id=OTHER_OWNER,
            secret_hash=hash_device_secret("attacker-secret"),
            enabled=True,
            current_version="1.1.0",
        )
    )

    stored = repo.get_by_device_id("dev-1")
    assert stored.owner_id == OWNER
    assert stored.secret_hash == original.secret_hash
    assert stored.enabled is False
    # And it did record the part that was the device's to report.
    assert stored.current_version == "1.1.0"


def test_set_enabled_switches_a_device_off_and_on(session):
    repo = SqliteDeviceRepository(session)
    register(repo)

    assert repo.set_enabled("dev-1", OWNER, False).enabled is False
    assert repo.set_enabled("dev-1", OWNER, True).enabled is True


def test_another_account_cannot_disable_your_device(session):
    repo = SqliteDeviceRepository(session)
    register(repo)

    assert repo.set_enabled("dev-1", OTHER_OWNER, False) is None
    assert repo.get_by_device_id("dev-1").enabled is True


def test_set_enabled_returns_none_for_an_unknown_device(session):
    repo = SqliteDeviceRepository(session)

    assert repo.set_enabled("never-registered", OWNER, False) is None


def test_get_by_device_id_returns_none_when_missing(session):
    repo = SqliteDeviceRepository(session)

    assert repo.get_by_device_id("unknown") is None


def test_device_list_all_orders_most_recently_seen_first(session):
    repo = SqliteDeviceRepository(session)
    older = datetime(2026, 7, 1, 12, 0, 0)
    newer = datetime(2026, 7, 2, 12, 0, 0)
    for device_id, last_seen in [("dev-old", older), ("dev-new", newer), ("dev-never", None)]:
        register(repo, device_id)
        repo.record_checkin(Device(device_id=device_id, model="ESP32", last_seen=last_seen))

    listed = repo.list_all(OWNER)

    assert [d.device_id for d in listed] == ["dev-new", "dev-old", "dev-never"]


def make_user(email="alice@example.com", is_superuser=False) -> User:
    return User(email=email, hashed_password="hash", is_superuser=is_superuser)


def test_user_add_assigns_id_and_round_trips(session):
    repo = SqliteUserRepository(session)

    added = repo.add(make_user(is_superuser=True))

    assert added.id is not None
    fetched = repo.get_by_email("alice@example.com")
    assert fetched == added
    assert fetched.is_superuser is True
    assert repo.get_by_id(added.id) == added


def test_user_add_rejects_a_duplicate_email(session):
    repo = SqliteUserRepository(session)
    repo.add(make_user())

    with pytest.raises(UserAlreadyExists):
        repo.add(make_user())


def test_get_user_by_email_returns_none_when_missing(session):
    repo = SqliteUserRepository(session)

    assert repo.get_by_email("nobody@example.com") is None


def test_the_email_index_is_what_refuses_a_second_spelling(session):
    """The lookup is exact, so normalizing on write is not a convenience.

    `SyncUserDatabase` lowercases before it reaches here. Were that dropped,
    this insert would succeed and one inbox would hold two accounts, which is
    why the repository is tested against the raw form rather than the tidy one.
    """
    repo = SqliteUserRepository(session)
    repo.add(make_user())

    assert repo.get_by_email("Alice@Example.com") is None
    repo.add(make_user(email="Alice@Example.com"))


def test_user_update_writes_a_changed_hash_back(session):
    repo = SqliteUserRepository(session)
    user = repo.add(make_user())
    user.hashed_password = "a-new-hash"

    repo.update(user)

    assert repo.get_by_id(user.id).hashed_password == "a-new-hash"


def test_user_update_refuses_to_move_an_account_onto_a_taken_email(session):
    repo = SqliteUserRepository(session)
    repo.add(make_user("alice@example.com"))
    bob = repo.add(make_user("bob@example.com"))
    bob.email = "alice@example.com"

    with pytest.raises(UserAlreadyExists):
        repo.update(bob)


def test_user_update_rejects_an_id_with_no_row(session):
    repo = SqliteUserRepository(session)
    ghost = make_user()
    ghost.id = 999

    with pytest.raises(UserNotFound):
        repo.update(ghost)


def test_deleting_a_user_takes_its_refresh_handles_with_it(session):
    """SQLite ignores the foreign key, so the repository has to do this itself.

    A handle left behind still names a user id, and ids are reused, so the next
    account created would inherit a live session belonging to nobody.
    """
    users = SqliteUserRepository(session)
    tokens = SqliteRefreshTokenRepository(session)
    user = users.add(make_user())
    tokens.add(RefreshToken(token="handle", user_id=user.id))

    users.delete(user)

    assert users.get_by_id(user.id) is None
    assert tokens.get("handle") is None


def test_refresh_token_round_trips_and_is_stamped(session):
    repo = SqliteRefreshTokenRepository(session)
    user = SqliteUserRepository(session).add(make_user())

    stored = repo.add(RefreshToken(token="handle", user_id=user.id))

    assert stored.created_at.tzinfo is not None
    assert repo.get("handle").user_id == user.id


def test_refresh_token_delete_is_silent_on_an_unknown_handle(session):
    repo = SqliteRefreshTokenRepository(session)

    repo.delete("never-issued")


def test_refresh_token_delete_for_user_leaves_other_accounts_alone(session):
    users = SqliteUserRepository(session)
    repo = SqliteRefreshTokenRepository(session)
    alice = users.add(make_user("alice@example.com"))
    bob = users.add(make_user("bob@example.com"))
    repo.add(RefreshToken(token="alice-1", user_id=alice.id))
    repo.add(RefreshToken(token="alice-2", user_id=alice.id))
    repo.add(RefreshToken(token="bob-1", user_id=bob.id))

    repo.delete_for_user(alice.id)

    assert repo.get("alice-1") is None
    assert repo.get("alice-2") is None
    assert repo.get("bob-1") is not None


def test_every_repository_hands_back_aware_timestamps(session):
    """SQLite has no timezone type, so a round-trip strips the offset.

    Re-attaching it is the repository's job. If it were each caller's, the
    routes would have to remember, and one of them would eventually not.
    """
    firmware = SqliteFirmwareRepository(session).add(make_firmware())
    user = SqliteUserRepository(session).add(make_user())
    devices = SqliteDeviceRepository(session)
    registered = register(devices, "aa:bb:cc")
    device = devices.record_checkin(
        Device(
            device_id="aa:bb:cc",
            model="ESP32",
            current_version="1.0.0",
            last_seen=datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert firmware.created_at.tzinfo is not None
    assert user.created_at.tzinfo is not None
    assert registered.registered_at.tzinfo is not None
    assert device.last_seen == datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def make_event(device_id="dev-1", event_type=EventType.CHECK, **overrides) -> DeviceEvent:
    return DeviceEvent(device_id=device_id, event_type=event_type, **overrides)


def test_event_repo_stamps_utc_on_append(session):
    repo = SqliteDeviceEventRepository(session)

    event = repo.add(make_event(from_version="1.0.0", to_version="1.1.0"))

    assert event.id is not None
    assert event.created_at.tzinfo is timezone.utc
    assert event.event_type is EventType.CHECK


def test_event_repo_returns_one_device_history_newest_first(session):
    repo = SqliteDeviceEventRepository(session)
    repo.add(make_event(event_type=EventType.CHECK))
    repo.add(make_event(event_type=EventType.DOWNLOAD))
    repo.add(make_event(event_type=EventType.SUCCESS))
    repo.add(make_event(device_id="other", event_type=EventType.CHECK))

    history = repo.list_for_device("dev-1")

    # Ordered by id: events from one check-in share a timestamp at the
    # resolution SQLite keeps, so insertion order is the only thing that
    # separates them.
    assert [e.event_type for e in history] == [
        EventType.SUCCESS,
        EventType.DOWNLOAD,
        EventType.CHECK,
    ]


def test_event_repo_finds_the_latest_of_one_type(session):
    repo = SqliteDeviceEventRepository(session)
    repo.add(make_event(event_type=EventType.DOWNLOAD, to_version="1.0.0"))
    repo.add(make_event(event_type=EventType.SUCCESS, to_version="1.0.0"))
    repo.add(make_event(event_type=EventType.DOWNLOAD, to_version="1.2.0"))

    latest = repo.latest_for_device("dev-1", EventType.DOWNLOAD)

    assert latest.to_version == "1.2.0"


def test_event_repo_answers_none_for_a_device_with_no_such_event(session):
    repo = SqliteDeviceEventRepository(session)
    repo.add(make_event(event_type=EventType.CHECK))

    assert repo.latest_for_device("dev-1", EventType.ROLLBACK) is None
    assert repo.list_for_device("never-seen") == []


def test_event_repo_accepts_an_unattributed_download(session):
    """A cached or hand-typed download URL carries no device id."""
    repo = SqliteDeviceEventRepository(session)

    event = repo.add(make_event(device_id=None, event_type=EventType.DOWNLOAD))

    assert event.device_id is None
