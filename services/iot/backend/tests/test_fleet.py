from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from domain.fleet import (
    MIN_OFFLINE_SECONDS,
    MISSED_CHECKINS_BEFORE_OFFLINE,
    is_online,
    summarize,
)
from domain.models import Device

NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)


def check_in(seconds_ago: float) -> datetime:
    return NOW - timedelta(seconds=seconds_ago)


@pytest.mark.parametrize(
    "last_seen, poll_interval_seconds",
    [
        (None, 6),
        (check_in(1), None),
        (None, None),
    ],
    ids=["never-checked-in", "interval-not-reported", "neither"],
)
def test_unknowable_state_is_none_not_offline(last_seen, poll_interval_seconds):
    assert is_online(last_seen, poll_interval_seconds, NOW) is None


@pytest.mark.parametrize("poll_interval_seconds", [0, -6])
def test_nonsense_interval_is_none(poll_interval_seconds):
    """A zero interval would make the threshold collapse to the floor for no stated reason."""
    assert is_online(check_in(1), poll_interval_seconds, NOW) is None


def test_device_that_just_reported_is_online():
    assert is_online(check_in(1), 6, NOW) is True


def test_device_silent_past_the_threshold_is_offline():
    # 6s interval, 3 missed check-ins allowed, floored at 15s: 18s is the line.
    assert is_online(check_in(19), 6, NOW) is False


def test_a_few_missed_checkins_are_tolerated():
    """One slow TLS handshake on the ESP32 must not flip the dashboard."""
    assert is_online(check_in(7), 6, NOW) is True


def test_slow_polling_device_is_not_held_to_the_floor():
    """The floor raises a short threshold, it never lowers a long one.

    A device polling every 60s is silent for most of every minute by design.
    Comparing it against MIN_OFFLINE_SECONDS instead of its own interval would
    show it offline three quarters of the time.
    """
    assert MIN_OFFLINE_SECONDS < 60
    assert is_online(check_in(MIN_OFFLINE_SECONDS + 1), 60, NOW) is True


def test_fast_polling_device_gets_the_floor():
    """Three missed check-ins at a 1s interval is 3s, which one retry can spend."""
    assert MISSED_CHECKINS_BEFORE_OFFLINE * 1 < MIN_OFFLINE_SECONDS
    assert is_online(check_in(MIN_OFFLINE_SECONDS - 1), 1, NOW) is True
    assert is_online(check_in(MIN_OFFLINE_SECONDS + 1), 1, NOW) is False


def device(model="ESP32", version="1.0.0", seconds_ago=1.0, interval=6) -> Device:
    return Device(
        device_id=f"dev-{model}-{version}-{seconds_ago}",
        model=model,
        current_version=version,
        last_seen=None if seconds_ago is None else check_in(seconds_ago),
        poll_interval_seconds=interval,
    )


def test_every_device_lands_in_exactly_one_state():
    stats = summarize(
        [
            device(seconds_ago=1),
            device(seconds_ago=1),
            device(seconds_ago=600),
            device(interval=None),
        ],
        {"ESP32": "1.0.0"},
        NOW,
    )

    assert (stats.online, stats.offline, stats.unknown) == (2, 1, 1)
    assert stats.online + stats.offline + stats.unknown == stats.total


def test_behind_latest_counts_devices_an_update_would_be_offered_to():
    stats = summarize(
        [device(version="1.0.0"), device(version="1.2.0")],
        {"ESP32": "1.2.0"},
        NOW,
    )

    assert stats.behind_latest == 1


def test_behind_latest_compares_as_tuples_not_strings():
    """1.2.10 is ahead of 1.2.9, which string comparison reads backwards."""
    stats = summarize([device(version="1.2.10")], {"ESP32": "1.2.9"}, NOW)

    assert stats.behind_latest == 0


@pytest.mark.parametrize(
    "version, latest",
    [
        ("1.0.0", None),
        ("2.0.0", "1.0.0"),
        (None, "1.0.0"),
    ],
    ids=["model-has-nothing-published", "device-ahead-of-latest", "device-reported-no-version"],
)
def test_a_device_with_no_offer_waiting_is_not_behind(version, latest):
    """`behind_latest` has to agree with what the next check-in actually gets."""
    stats = summarize([device(version=version)], {"ESP32": latest}, NOW)

    assert stats.total == 1
    assert stats.behind_latest == 0


def test_offline_devices_still_count_as_behind():
    """Being unreachable is not being up to date; the update is still pending."""
    stats = summarize([device(version="1.0.0", seconds_ago=600)], {"ESP32": "1.1.0"}, NOW)

    assert stats.offline == 1
    assert stats.behind_latest == 1


def test_an_empty_fleet_is_all_zeroes():
    stats = summarize([], {}, NOW)

    assert (stats.total, stats.online, stats.offline, stats.unknown, stats.behind_latest) == (
        0,
        0,
        0,
        0,
        0,
    )
