from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest
from domain.firmware_image import (
    APP_DESC_MAGIC,
    APP_DESC_OFFSET,
    CHIP_ID_OFFSET,
    HASH_APPENDED_OFFSET,
    IMAGE_DIGEST_BYTES,
    IMAGE_MAGIC,
    MIN_FIRMWARE_BYTES,
    InvalidFirmwareImage,
    read_build_tag,
    validate_image,
)

CHIP_ID_ESP32S3 = 0x0009

# The ESP32-S3 build this project targets; git-ignored, so present only on a
# machine that has run arduino-cli.
REAL_IMAGE = (
    Path(__file__).resolve().parents[2] / "esp32/main/build/esp32.esp32.esp32s3/main.ino.bin"
)


def make_image(
    *,
    magic: int = IMAGE_MAGIC,
    chip_id: int = CHIP_ID_ESP32S3,
    app_desc_magic: int = APP_DESC_MAGIC,
    hash_appended: bool = True,
    size: int = MIN_FIRMWARE_BYTES,
) -> bytes:
    """Build an image carrying only the fields `validate_image` reads.

    Every field is a keyword so a test can break exactly one of them.
    """
    image = bytearray(size)
    image[0] = magic
    struct.pack_into("<H", image, CHIP_ID_OFFSET, chip_id)
    image[HASH_APPENDED_OFFSET] = 1 if hash_appended else 0
    struct.pack_into("<I", image, APP_DESC_OFFSET, app_desc_magic)
    if hash_appended:
        body = bytes(image[:-IMAGE_DIGEST_BYTES])
        image[-IMAGE_DIGEST_BYTES:] = hashlib.sha256(body).digest()
    return bytes(image)


def test_validate_image_accepts_a_well_formed_image():
    validate_image(make_image())


def test_validate_image_accepts_an_image_without_an_appended_digest():
    # hash_appended is a build option, so its absence is not an error.
    validate_image(make_image(hash_appended=False))


def test_validate_image_rejects_empty_bytes():
    # Every other check indexes a fixed offset, so a length check running
    # second would raise IndexError and surface as a 500.
    with pytest.raises(InvalidFirmwareImage):
        validate_image(b"")


def test_validate_image_rejects_a_file_below_the_size_floor():
    with pytest.raises(InvalidFirmwareImage):
        validate_image(make_image()[: MIN_FIRMWARE_BYTES - 1])


def test_validate_image_rejects_a_wrong_leading_magic_byte():
    with pytest.raises(InvalidFirmwareImage):
        validate_image(make_image(magic=0x7F))


def test_validate_image_rejects_bytes_with_only_the_leading_magic_byte():
    # Right first byte and nothing else clears a bare magic-byte check, which
    # is why the app descriptor is required too.
    with pytest.raises(InvalidFirmwareImage):
        validate_image(bytes([IMAGE_MAGIC]) + bytes(MIN_FIRMWARE_BYTES))


def test_validate_image_rejects_an_image_without_an_app_descriptor():
    with pytest.raises(InvalidFirmwareImage):
        validate_image(make_image(app_desc_magic=0x00000000))


def test_validate_image_rejects_an_unknown_chip_id():
    with pytest.raises(InvalidFirmwareImage):
        validate_image(make_image(chip_id=0xFFFF))


def test_validate_image_rejects_a_corrupted_body():
    image = bytearray(make_image())
    image[600] ^= 0xFF

    with pytest.raises(InvalidFirmwareImage):
        validate_image(bytes(image))


def test_validate_image_rejects_a_truncated_image():
    # Truncation leaves the header intact, so the digest is the only check
    # that catches it.
    image = make_image(size=MIN_FIRMWARE_BYTES * 2)

    with pytest.raises(InvalidFirmwareImage):
        validate_image(image[:-64] + image[-IMAGE_DIGEST_BYTES:])


@pytest.mark.skipif(not REAL_IMAGE.exists(), reason="no local arduino-cli build to check against")
def test_validate_image_accepts_a_real_arduino_cli_build():
    # Guards the assumption the synthesized images above rest on: that this is
    # the layout arduino-cli actually emits.
    validate_image(REAL_IMAGE.read_bytes())


def tagged(model: str = "ESP32", version: str = "1.0.4") -> bytes:
    """The marker as `ota.cpp` builds it, buried where a real one sits."""
    marker = f"ESPOTA-BUILD{{model={model};version={version}}}".encode("ascii")
    image = bytearray(make_image(size=MIN_FIRMWARE_BYTES * 2))
    image[600 : 600 + len(marker)] = marker
    return bytes(image)


def test_read_build_tag_reads_what_the_image_says_it_is():
    tag = read_build_tag(tagged(model="ESP32-S3", version="2.4.2"))

    assert tag.model == "ESP32-S3"
    assert tag.version == "2.4.2"


def test_read_build_tag_answers_none_for_an_image_without_one():
    # Builds from before the marker existed, and anything from another
    # toolchain. The caller falls back to what the uploader typed.
    assert read_build_tag(make_image()) is None


def test_read_build_tag_refuses_an_image_carrying_two():
    # An application image carries exactly one, so two means the bytes are a
    # concatenation or were edited, and picking a winner would be a guess.
    doubled = tagged(version="1.0.4") + tagged(version="9.9.9")

    with pytest.raises(InvalidFirmwareImage):
        read_build_tag(doubled)


def test_read_build_tag_ignores_a_marker_that_never_closes():
    # A prefix appearing in unrelated data is not a marker. Without the closing
    # brace the pattern must not run on and swallow whatever follows.
    image = bytearray(make_image(size=MIN_FIRMWARE_BYTES * 2))
    opener = b"ESPOTA-BUILD{model=ESP32;version=1.0.4"
    image[600 : 600 + len(opener)] = opener

    assert read_build_tag(bytes(image)) is None


@pytest.mark.skipif(not REAL_IMAGE.exists(), reason="no local arduino-cli build to check against")
def test_read_build_tag_finds_the_marker_in_a_real_arduino_cli_build():
    # The claim the whole feature rests on: the string survives the compiler
    # and the linker's --gc-sections, which dropped it until initOTA() printed
    # it. A synthesized image cannot tell us that.
    tag = read_build_tag(REAL_IMAGE.read_bytes())

    assert tag is not None
    assert tag.model == "ESP32"
