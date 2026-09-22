"""Structural validation of an uploaded ESP32 application image.

Nothing downstream can tell a bad file from a good one: a signature over
garbage is still a valid signature, so the first thing to object is
`Update.end()` on the device, reported as a misleading "Decryption error".

This is mistake detection, not authenticity. Every field below is plain data
anyone can write, and `esptool.py` emits images satisfying all of it. It says
nothing about who produced an image; that is the signature's job.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass

# Image layout, little-endian throughout, from `esp_app_format.h` in the ESP32
# Arduino core:
#
#   0   esp_image_header_t          24 bytes, opens with the magic byte
#   12  chip_id                     uint16, an esp_chip_id_t
#   23  hash_appended               1 if a trailing SHA-256 covers the image
#   24  first segment header        8 bytes
#   32  esp_app_desc_t              opens with its own magic word
#
# The app descriptor is what separates an application image from a bootloader
# or a partition table, which share the 0xE9 header magic.
IMAGE_MAGIC = 0xE9
CHIP_ID_OFFSET = 12
HASH_APPENDED_OFFSET = 23
APP_DESC_OFFSET = 32
APP_DESC_MAGIC = 0xABCD5432

# The trailing digest, when present, covers every byte before it.
IMAGE_DIGEST_BYTES = 32

# esp_chip_id_t. Not a model-to-chip mapping, which the project has no table
# for: this only asks whether the target chip exists.
KNOWN_CHIP_IDS = frozenset(
    {
        0x0000,  # ESP32
        0x0002,  # ESP32-S2
        0x0005,  # ESP32-C3
        0x0009,  # ESP32-S3
        0x000C,  # ESP32-C2
        0x000D,  # ESP32-C6
        0x0010,  # ESP32-H2
        0x0012,  # ESP32-P4
        0x0014,  # ESP32-C61
        0x0017,  # ESP32-C5
        0x0019,  # ESP32-H21
        0x001C,  # ESP32-H4
    }
)

# A sanity floor, not a format requirement. A real image runs to hundreds of
# kilobytes. It also keeps the fixed offsets above in bounds.
MIN_FIRMWARE_BYTES = 1024

# The matching ceiling. Derived from the largest image any chip above could
# boot, not from this project's own app partition (0x150000): the server
# publishes for whatever models it is told about and has no business knowing
# one partition table. The largest external flash on that list is the P4's
# 64 MB, and an OTA layout needs two app slots, so no bootable image reaches
# 32 MiB. Sizing it to a real build instead would reject a legitimate one the
# day a board with more flash appears, which is a worse failure than the
# oversized upload this exists to stop.
MAX_FIRMWARE_BYTES = 32 * 1024 * 1024


# The marker `esp32/main/ota.cpp` builds out of its own FIRMWARE_VERSION and
# DEVICE_MODEL, and prints at boot so the linker keeps it. Both values are in
# the binary as plain strings without it, but so is the Arduino core's own
# version, and nothing tells them apart. This is what makes them findable.
BUILD_TAG_PATTERN = re.compile(rb"ESPOTA-BUILD\{model=([^;}]{1,64});version=([^;}]{1,32})\}")


class InvalidFirmwareImage(Exception):
    """Raised when uploaded bytes are not a well-formed ESP32 application image."""


@dataclass(frozen=True)
class BuildTag:
    model: str
    version: str


def read_build_tag(data: bytes) -> BuildTag | None:
    """What the image says it is, or None if it carries no marker.

    The image is the authority on this wherever it answers, since it is the
    same string the device will report on its next check-in. A build without
    the marker predates it or came from another toolchain, and the caller falls
    back to what the uploader typed.

    A second marker means the bytes are not what they claim: an application
    image carries exactly one, so anything else is a concatenation or a
    doctored file, and guessing which one is real is not this function's call.
    """
    matches = BUILD_TAG_PATTERN.findall(data)
    if not matches:
        return None
    if len(matches) > 1:
        raise InvalidFirmwareImage(
            f"Image carries {len(matches)} build markers; exactly one is expected"
        )

    model, version = matches[0]
    try:
        return BuildTag(model.decode("ascii"), version.decode("ascii"))
    except UnicodeDecodeError as exc:
        # The device builds this string from C literals, so anything else came
        # from a byte sequence that happens to look like the marker.
        raise InvalidFirmwareImage("Image build marker is not ASCII") from exc


def validate_image(data: bytes) -> None:
    """Raise `InvalidFirmwareImage` unless `data` is a well-formed ESP32 image.

    The length check has to come first: it is what keeps the later reads in
    bounds, so reordering turns an empty upload into an IndexError.
    """
    if len(data) < MIN_FIRMWARE_BYTES:
        raise InvalidFirmwareImage(
            f"File is {len(data)} bytes, below the {MIN_FIRMWARE_BYTES}-byte minimum "
            "for an ESP32 image"
        )

    if data[0] != IMAGE_MAGIC:
        raise InvalidFirmwareImage(
            f"Not an ESP32 image: expected magic 0x{IMAGE_MAGIC:02X}, found 0x{data[0]:02X}"
        )

    app_desc_magic = struct.unpack_from("<I", data, APP_DESC_OFFSET)[0]
    if app_desc_magic != APP_DESC_MAGIC:
        raise InvalidFirmwareImage(
            "Not an ESP32 application image: no app descriptor at offset "
            f"{APP_DESC_OFFSET} (expected 0x{APP_DESC_MAGIC:08X}, found 0x{app_desc_magic:08X})"
        )

    chip_id = struct.unpack_from("<H", data, CHIP_ID_OFFSET)[0]
    if chip_id not in KNOWN_CHIP_IDS:
        raise InvalidFirmwareImage(f"Image targets unknown chip id 0x{chip_id:04X}")

    # ESP-IDF's "simple hash", scoped to corruption only. A secure-boot
    # signature, where there is one, sits after it.
    if data[HASH_APPENDED_OFFSET] == 1:
        body, digest = data[:-IMAGE_DIGEST_BYTES], data[-IMAGE_DIGEST_BYTES:]
        if hashlib.sha256(body).digest() != digest:
            raise InvalidFirmwareImage(
                "Image checksum does not match its contents; the file is corrupt or truncated"
            )
