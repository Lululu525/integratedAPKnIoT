from __future__ import annotations

import pytest
from domain.models import EventType
from domain.ota_history import classify_version_change


@pytest.mark.parametrize(
    "previous, reported",
    [
        (None, "1.0.0"),
        ("1.0.0", "1.0.0"),
    ],
    ids=["first-sighting", "unchanged"],
)
def test_nothing_worth_recording(previous, reported):
    assert classify_version_change(previous, reported) is None


def test_moving_up_is_a_successful_update():
    assert classify_version_change("1.0.0", "1.1.0") is EventType.SUCCESS


def test_moving_down_is_a_rollback():
    assert classify_version_change("1.1.0", "1.0.0") is EventType.ROLLBACK


def test_versions_compare_as_tuples_not_strings():
    """1.2.10 is ahead of 1.2.9. Read as strings this is a rollback."""
    assert classify_version_change("1.2.9", "1.2.10") is EventType.SUCCESS


def test_two_strings_that_parse_the_same_are_not_a_rollback():
    """`parse_version` is lenient by contract, so different strings can tie.

    A device reporting something unparseable would otherwise log a rollback on
    every check-in, forever.
    """
    assert classify_version_change("1.0.0", "1.0.0-dirty") is None
    assert classify_version_change("garbage", "nonsense") is None
