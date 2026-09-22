from __future__ import annotations

import base64
import struct

import pytest
from application.upload_firmware import (
    InvalidUploadIdentity,
    NoPublicKey,
    UploadFirmware,
    UploadFirmwareRequest,
)
from conftest import FakeFirmwareRepository, FakeStorage
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from domain import signing
from domain.firmware_image import (
    APP_DESC_MAGIC,
    APP_DESC_OFFSET,
    CHIP_ID_OFFSET,
    IMAGE_MAGIC,
    MIN_FIRMWARE_BYTES,
    InvalidFirmwareImage,
    read_build_tag,
)
from domain.models import Firmware
from ports.repository import FirmwareAlreadyExists, FirmwareBinaryAlreadyExists


def valid_image(filler: int = 0) -> bytes:
    """Minimal bytes that clear `validate_image`; the contents carry no meaning.

    Leaves the appended-digest flag clear, a legitimate build option, so there
    is no digest to keep in step with the padding. `filler` varies the payload
    so two calls differ in nothing but their hash.
    """
    image = bytearray([filler] * MIN_FIRMWARE_BYTES)
    image[0] = IMAGE_MAGIC
    struct.pack_into("<H", image, CHIP_ID_OFFSET, 0x0009)
    struct.pack_into("<I", image, APP_DESC_OFFSET, APP_DESC_MAGIC)
    return bytes(image)


class RejectingFirmwareRepository(FakeFirmwareRepository):
    """Stands in for the unique (model, version) index rejecting an add."""

    def add(self, firmware: Firmware) -> Firmware:
        raise FirmwareAlreadyExists(firmware.model, firmware.version)


class RacingFirmwareRepository(FakeFirmwareRepository):
    """Empty to every read, rejecting on write, which is what the race looks like.

    Seeding a row instead would make `get_by_sha256` answer and the use case
    would never reach `add`.
    """

    def add(self, firmware: Firmware) -> Firmware:
        raise FirmwareBinaryAlreadyExists(firmware.model, "1.0.2")


# The account every request in this module publishes as. Ownership is not what
# these tests are about, but it is required, so it is named once.
OWNER = 1


def repository_already_holding(data: bytes, model="ESP32", version="1.0.2"):
    """A repository whose rows already contain exactly these bytes."""
    sha256 = signing.calculate_sha256_bytes(data)
    return FakeFirmwareRepository(
        [
            Firmware(
                owner_id=OWNER,
                download_id="already-here",
                model=model,
                version=version,
                filename=f"{sha256}.bin",
                signature="s",
                sha256=sha256,
                size_bytes=len(data),
                id=1,
            )
        ]
    )


@pytest.fixture(scope="module")
def keypair():
    """The uploader's key pair. The server only ever sees the public half."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_key, private_pem, public_pem


def upload_request(keypair, data, model=None, version=None, **overrides):
    """A request carrying the signature the uploader would really have produced.

    Signed over the model and version the use case is about to resolve, not
    over what the form typed, since those differ whenever an image names
    itself. Tests about a bad signature pass their own through `signature=`.
    """
    _, private_pem, public_pem = keypair
    tag = read_build_tag(data)
    resolved_model = model or (tag.model if tag else None)
    resolved_version = version or (tag.version if tag else None)

    signature = overrides.pop("signature", None)
    if signature is None and resolved_model and resolved_version:
        signature = signing.sign_manifest(
            resolved_model,
            resolved_version,
            signing.calculate_sha256_bytes(data),
            private_pem,
        )

    return UploadFirmwareRequest(
        owner_id=OWNER,
        owner_public_key=overrides.pop("owner_public_key", public_pem),
        signature=signature or "",
        model=model,
        version=version,
        data=data,
        original_filename=overrides.pop("original_filename", "firmware.bin"),
        **overrides,
    )


def test_execute_stores_data_under_its_content_hash(keypair):
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    data = valid_image()

    use_case.execute(
        upload_request(
            keypair,
            model="ESP32",
            version="1.0.0",
            original_filename="firmware.bin",
            data=data,
        )
    )

    assert storage.files == {f"{signing.calculate_sha256_bytes(data)}.bin": data}


def test_execute_stores_nothing_when_the_version_is_malformed(keypair):
    """A `v` prefix parses to (0, 0, 0) on the device, so it would lose to every
    real release and reach nothing.

    Reached through the fallback: this image carries no marker, so the typed
    version is the one that gets checked.
    """
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(signing.InvalidManifestField):
        use_case.execute(
            upload_request(
                keypair,
                model="ESP32",
                version="v1.0.0",
                original_filename="firmware.bin",
                data=valid_image(),
            )
        )

    assert storage.files == {}
    assert repo.added == []


def test_two_uploads_in_the_same_second_do_not_overwrite_each_other(keypair):
    """The #29 case: distinct binaries, one model, no clock separating them.

    Under timestamp naming both landed on one file and the first row served the
    second's bytes, failing signature verification on-device forever.
    """
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    first, second = valid_image(0x11), valid_image(0x22)

    for version, data in (("1.0.0", first), ("1.0.1", second)):
        use_case.execute(
            upload_request(
                keypair,
                model="ESP32",
                version=version,
                original_filename="main.ino.bin",
                data=data,
            )
        )

    assert len(storage.files) == 2
    for firmware, expected in zip(repo.added, (first, second), strict=True):
        assert storage.get(firmware.filename) == expected
        assert signing.calculate_sha256_bytes(storage.get(firmware.filename)) == firmware.sha256


def test_execute_records_firmware_with_matching_hash_and_verifiable_signature(keypair):
    private_key, _, _ = keypair
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    data = valid_image()

    firmware = use_case.execute(
        upload_request(
            keypair,
            model="ESP32",
            version="1.0.0",
            original_filename="firmware.bin",
            data=data,
        )
    )

    assert firmware is repo.added[0]
    assert firmware.model == "ESP32"
    assert firmware.version == "1.0.0"
    assert firmware.sha256 == signing.calculate_sha256_bytes(data)
    assert firmware.filename == f"{firmware.sha256}.bin"
    assert firmware.original_filename == "firmware.bin"

    # Stored exactly as supplied, and it still verifies under the uploader's
    # key. What is on the row is what the device will check, so a server that
    # re-encoded it here would break verification on hardware and nowhere else.
    manifest_bytes = signing.build_manifest("ESP32", "1.0.0", firmware.sha256).encode("utf-8")
    private_key.public_key().verify(
        base64.b64decode(firmware.signature),
        manifest_bytes,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO),
        hashes.SHA256(),
    )


def test_execute_records_size_and_notes(keypair):
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    data = valid_image()

    firmware = use_case.execute(
        upload_request(
            keypair,
            model="ESP32",
            version="1.0.0",
            original_filename="firmware.bin",
            data=data,
            notes="  Fix SNTP retry storm  ",
        )
    )

    assert firmware.size_bytes == len(data)
    assert firmware.notes == "Fix SNTP retry storm"


def test_execute_normalizes_blank_notes_to_none(keypair):
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    firmware = use_case.execute(
        upload_request(
            keypair,
            model="ESP32",
            version="1.0.0",
            original_filename="firmware.bin",
            data=valid_image(),
            notes="   ",
        )
    )

    assert firmware.notes is None


def test_execute_records_no_notes_as_none(keypair):
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    firmware = use_case.execute(
        upload_request(
            keypair,
            model="ESP32",
            version="1.0.0",
            original_filename="firmware.bin",
            data=valid_image(),
        )
    )

    assert firmware.notes is None


def test_execute_keeps_the_stored_blob_when_the_version_is_taken(keypair):
    """A content-addressed blob may already back another row, so it stays put.

    Deleting one a concurrent upload has committed a row against makes that row
    404, which reboot-loops every device mid-download.
    """
    repo, storage = RejectingFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    data = valid_image()

    with pytest.raises(FirmwareAlreadyExists):
        use_case.execute(
            upload_request(
                keypair,
                model="ESP32",
                version="1.0.0",
                original_filename="firmware.bin",
                data=data,
            )
        )

    assert storage.files == {f"{signing.calculate_sha256_bytes(data)}.bin": data}


def test_execute_stores_nothing_when_the_signature_does_not_verify(keypair):
    """Verified before the blob is written, so a rejection leaves no orphan.

    The step that can fail on the contents runs before anything reaches disk.
    """
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(signing.SignatureRejected):
        use_case.execute(
            upload_request(
                keypair,
                valid_image(),
                model="ESP32",
                version="1.0.0",
                signature=base64.b64encode(b"not a signature").decode(),
            )
        )

    assert storage.files == {}
    assert repo.added == []


def test_execute_refuses_a_signature_that_is_not_base64(keypair):
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(signing.SignatureRejected):
        use_case.execute(
            upload_request(
                keypair, valid_image(), model="ESP32", version="1.0.0", signature="not base64!!"
            )
        )


def test_execute_refuses_a_signature_made_with_another_account_s_key(keypair):
    """The property multi-tenant signing exists for.

    One server-held key signed for everybody, so a leak was every tenant's
    problem at once. With the key on the account, a build signed by A is not
    publishable under B even by an authenticated B.
    """
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    data = valid_image()
    foreign = signing.sign_manifest(
        "ESP32", "1.0.0", signing.calculate_sha256_bytes(data), other_pem
    )

    with pytest.raises(signing.SignatureRejected):
        use_case.execute(
            upload_request(keypair, data, model="ESP32", version="1.0.0", signature=foreign)
        )

    assert storage.files == {}


def test_execute_refuses_a_signature_over_different_bytes(keypair):
    """A signature is bound to a hash, so pointing it at another file fails.

    The realistic version is an operator signing one build and then picking a
    different .bin in the form without signing again.
    """
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    _, private_pem, _ = keypair
    stale = signing.sign_manifest(
        "ESP32", "1.0.0", signing.calculate_sha256_bytes(valid_image(1)), private_pem
    )

    with pytest.raises(signing.SignatureRejected):
        use_case.execute(
            upload_request(keypair, valid_image(2), model="ESP32", version="1.0.0", signature=stale)
        )


def test_execute_refuses_an_account_with_no_public_key(keypair):
    """There is no server key to fall back to, so this is a refusal, not a default."""
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(NoPublicKey):
        use_case.execute(
            upload_request(
                keypair, valid_image(), model="ESP32", version="1.0.0", owner_public_key=None
            )
        )

    assert storage.files == {}


def test_execute_rejects_data_that_is_not_an_esp32_image(keypair):
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(InvalidFirmwareImage):
        use_case.execute(
            upload_request(
                keypair,
                model="ESP32",
                version="1.0.0",
                original_filename="firmware.bin",
                data=b"not an image",
            )
        )

    assert storage.files == {}
    assert repo.added == []


def test_execute_rejects_a_binary_already_stored_under_another_version(keypair):
    data = valid_image()
    repo, storage = repository_already_holding(data), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(FirmwareBinaryAlreadyExists) as exc_info:
        use_case.execute(
            upload_request(
                keypair,
                model="ESP32",
                version="1.0.3",
                original_filename="firmware.bin",
                data=data,
            )
        )

    assert exc_info.value.existing_version == "1.0.2"
    assert storage.files == {}
    assert repo.added == []


def test_execute_leaves_the_blob_when_the_index_rejects_the_binary(keypair):
    """The half of the duplicate check `get_by_sha256` cannot reach.

    A concurrent upload of the same bytes passes the pre-check, since that reads
    before it writes, and is rejected by `add` instead. The blob stays: it is
    named after its contents, so it is the file the row that won is served from.
    """
    data = valid_image()
    repo, storage = RacingFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(FirmwareBinaryAlreadyExists) as exc_info:
        use_case.execute(
            upload_request(
                keypair,
                model="ESP32",
                version="1.0.3",
                original_filename="firmware.bin",
                data=data,
            )
        )

    assert exc_info.value.existing_version == "1.0.2"
    assert storage.files == {f"{signing.calculate_sha256_bytes(data)}.bin": data}


def tagged_image(model: str = "ESP32", version: str = "1.0.5", filler: int = 0) -> bytes:
    """A valid image that names itself, the way a real build does."""
    marker = f"ESPOTA-BUILD{{model={model};version={version}}}".encode("ascii")
    image = bytearray(valid_image(filler))
    image[600 : 600 + len(marker)] = marker
    return bytes(image)


def test_execute_publishes_what_the_image_says_it_is(keypair):
    """The normal path: nothing is typed, so nothing can be mistyped."""
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    firmware = use_case.execute(
        upload_request(
            keypair,
            original_filename="main.ino.bin",
            data=tagged_image(model="ESP32-S3", version="2.4.2"),
        )
    )

    assert firmware.model == "ESP32-S3"
    assert firmware.version == "2.4.2"


def test_execute_signs_the_manifest_the_image_names(keypair):
    """The signature has to cover the resolved identity, not the typed one.

    A manifest signed over anything else verifies against nothing the device
    rebuilds, which is a signature failure on-device rather than a clear error
    here.
    """
    private_key, _, _ = keypair
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)
    data = tagged_image(model="ESP32-S3", version="2.4.2")

    firmware = use_case.execute(upload_request(keypair, data, original_filename="main.ino.bin"))

    manifest = signing.build_manifest("ESP32-S3", "2.4.2", firmware.sha256).encode("utf-8")
    private_key.public_key().verify(
        base64.b64decode(firmware.signature),
        manifest,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )


def test_execute_stores_nothing_when_the_label_contradicts_the_image(keypair):
    """Refused, not corrected. Storing it right under a wrong belief keeps the
    belief, and the next decision is made on it."""
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(InvalidUploadIdentity) as exc_info:
        use_case.execute(
            upload_request(
                keypair,
                original_filename="main.ino.bin",
                data=tagged_image(version="1.0.5"),
                model="ESP32",
                version="1.0.4",
            )
        )

    # Both values, since only the admin can tell which one is the mistake.
    assert "1.0.5" in str(exc_info.value)
    assert "1.0.4" in str(exc_info.value)
    assert storage.files == {}
    assert repo.added == []


def test_execute_accepts_a_label_that_agrees_with_the_image(keypair):
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    firmware = use_case.execute(
        upload_request(
            keypair,
            original_filename="main.ino.bin",
            data=tagged_image(version="1.0.5"),
            model="ESP32",
            version="1.0.5",
        )
    )

    assert firmware.version == "1.0.5"


def test_execute_needs_a_label_for_an_image_that_carries_none(keypair):
    """The fallback's own failure: nothing in the bytes says what they are."""
    repo, storage = FakeFirmwareRepository(), FakeStorage()
    use_case = UploadFirmware(repo, storage)

    with pytest.raises(InvalidUploadIdentity):
        use_case.execute(upload_request(keypair, valid_image(), original_filename="main.ino.bin"))

    assert storage.files == {}
    assert repo.added == []
