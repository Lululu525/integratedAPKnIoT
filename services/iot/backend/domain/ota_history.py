"""Reading what a device did from what it reports about itself.

The device tells the server one thing per check-in: the version it is running.
It never says "I finished an update" or "I rolled back". Both are visible only
as a change between two consecutive check-ins, which is what this file turns
into an event for the log.
"""

from __future__ import annotations

from domain.models import EventType
from domain.signing import compare_version


def classify_version_change(
    previous_version: str | None,
    reported_version: str,
) -> EventType | None:
    """Name the transition between a device's last two reported versions.

    None means nothing happened worth recording: a first sighting has no
    previous version to compare against, and an unchanged version is the normal
    case on every poll.

    `success` here means the device came up on a newer version than it was last
    seen on. The alternative reading, that it came up on exactly the version it
    was last offered, needs the offer to have been recorded and stays silent
    whenever a download could not be attributed. An upward move is observable
    for every device on every check-in, and the only way one happens in this
    system is a flash.
    """
    if previous_version is None or previous_version == reported_version:
        return None

    # Both directions are asked explicitly rather than deriving one from the
    # other. `parse_version` is lenient by contract and maps an unparseable
    # segment to 0, so two different strings can compare equal in both
    # directions, and that is not a rollback.
    if compare_version(reported_version, previous_version):
        return EventType.SUCCESS
    if compare_version(previous_version, reported_version):
        return EventType.ROLLBACK
    return None
