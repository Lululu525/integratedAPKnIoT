"""Fleet state the server works out instead of storing.

A device only ever reports facts about itself: when it last spoke, what
version it runs, how long it means to wait before speaking again. Whether
that adds up to "online" is a reading of those facts at the moment someone
asks, and it changes with no write happening anywhere, so there is no column
for it and no job that keeps one current.

The reading lives here rather than in the dashboard because the server is the
only place holding both halves of it. Handing the raw numbers to a frontend
that divides them itself puts a second copy of the rule one refactor away from
disagreeing with this one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from domain import signing
from domain.models import Device

# A single late check-in means a slow TLS handshake more often than it means a
# dead device, so allow a few before saying anything.
MISSED_CHECKINS_BEFORE_OFFLINE = 3

# Floor under the derived threshold. A device polling every second would
# otherwise be called offline over one retry.
MIN_OFFLINE_SECONDS = 15


def is_online(
    last_seen: datetime | None,
    poll_interval_seconds: int | None,
    now: datetime,
) -> bool | None:
    """Whether a device is still checking in on schedule.

    None means unknowable, not offline: a device that has never checked in, or
    one running firmware from before it reported its interval. Calling those
    offline would file a device that answered a second ago next to one that has
    been dark for a week.
    """
    if last_seen is None or poll_interval_seconds is None or poll_interval_seconds <= 0:
        return None

    offline_interval = (now - last_seen).total_seconds()
    if (
        offline_interval >= poll_interval_seconds * MISSED_CHECKINS_BEFORE_OFFLINE
        and offline_interval >= MIN_OFFLINE_SECONDS
    ):
        return False

    return True


@dataclass(frozen=True)
class FleetStats:
    """The device page's summary counts.

    `online + offline + unknown == total` holds by construction: every device
    falls into exactly one of the three, including the ones `is_online` cannot
    answer for. `behind_latest` cuts across all three, since a device can be
    behind whether or not it is currently reachable.
    """

    total: int
    online: int
    offline: int
    unknown: int
    behind_latest: int


def is_behind(current_version: str | None, latest_version: str | None) -> bool:
    """Whether this device would be offered an update on its next check.

    Mirrors the decision in `CheckUpdate.execute`, down to using the same
    compare, so the dashboard never reports a number the fleet disagrees with
    six seconds later. A model with nothing published and a device that has
    never said what it runs are both not behind: there is no offer to make.
    """
    if latest_version is None or current_version is None:
        return False
    return signing.compare_version(latest_version, current_version)


def summarize(
    devices: Iterable[Device],
    latest_versions: Mapping[str, str | None],
    now: datetime,
) -> FleetStats:
    """Tally the fleet against one clock reading and one set of latest versions.

    `latest_versions` maps a model to the version that would be offered for it,
    which is the caller's job to look up because the rule for picking it lives
    behind the firmware repository. A model missing from the mapping is treated
    as having nothing published.
    """
    total = online = offline = unknown = behind = 0

    for device in devices:
        total += 1
        state = is_online(device.last_seen, device.poll_interval_seconds, now)
        if state is None:
            unknown += 1
        elif state:
            online += 1
        else:
            offline += 1

        if is_behind(device.current_version, latest_versions.get(device.model)):
            behind += 1

    return FleetStats(
        total=total, online=online, offline=offline, unknown=unknown, behind_latest=behind
    )
