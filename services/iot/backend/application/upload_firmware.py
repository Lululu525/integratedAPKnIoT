"""Handle an account uploading a new firmware build.

Settles what the upload publishes as, computes its SHA-256, checks the
signature the uploader supplied against that account's public key, stores the
bytes under a name derived from the hash, and saves a firmware record pointing
at them.

Verify, not sign. The server holds no private key at all, so what it stores is
a signature produced by whoever built the image. That is the difference between
attesting who uploaded a file and attesting who built it, and it is what stops
a compromised server from producing firmware any device would accept.

The model and version are read out of the image itself wherever it carries a
build marker, so the label a build is stored under cannot drift from the one it
reports. See `_identify`.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain import signing
from domain.firmware_image import read_build_tag, validate_image
from domain.models import Firmware, new_download_id
from ports.repository import FirmwareBinaryAlreadyExists, FirmwareRepository
from ports.storage import StorageBackend


@dataclass
class UploadFirmwareRequest:
    original_filename: str
    data: bytes
    # Who is publishing. Everything stored is scoped to it, including both
    # uniqueness rules, so two accounts can each hold their own `ESP32 1.0.0`.
    owner_id: int
    # Base64 RSA-PSS over `model|version|sha256`, produced off-server by
    # `scripts/sign_firmware.py`, and the PEM it is checked against.
    signature: str
    owner_public_key: str | None
    # Both optional: an image that carries a build marker names itself, and
    # these are only read for one that does not.
    model: str | None = None
    version: str | None = None
    notes: str | None = None


class InvalidUploadIdentity(Exception):
    """The model and version to publish an upload under could not be settled."""


class NoPublicKey(Exception):
    """The account has not set the key its uploads would be verified against."""


class UploadFirmware:
    def __init__(self, repository: FirmwareRepository, storage: StorageBackend) -> None:
        self._repo = repository
        self._storage = storage

    def _identify(self, req: UploadFirmwareRequest) -> tuple[str, str]:
        """What to publish these bytes as.

        The image wins wherever it answers. Its marker is built from the same
        literals the device reports on its next check-in, so a label that
        disagrees with it is a label no device will ever ask for, and the
        server would go on offering an update that reinstalls itself forever.

        A disagreement is refused rather than quietly corrected. Storing the
        right thing under an operator who believes they published something
        else leaves the wrong belief in place for every decision after it.

        The typed fields are the fallback for an image with no marker, which
        means a build from before it existed or from another toolchain. That
        path is the hole this cannot close: nothing in such an image says what
        it is, so nothing can check the label against it.
        """
        tag = read_build_tag(req.data)
        if tag is None:
            if not req.model or not req.version:
                raise InvalidUploadIdentity(
                    "Image carries no build marker, so model and version must be given"
                )
            return req.model, req.version

        if (req.model and req.model != tag.model) or (req.version and req.version != tag.version):
            raise InvalidUploadIdentity(
                f"Image says {tag.model} {tag.version}, "
                f"upload says {req.model or '(blank)'} {req.version or '(blank)'}"
            )
        return tag.model, tag.version

    def execute(self, req: UploadFirmwareRequest) -> Firmware:
        # Structure first, since the identity is read out of the same bytes and
        # a file that is not an image has nothing to read.
        validate_image(req.data)
        model, version = self._identify(req)
        signing.validate_manifest_fields(model, version)

        sha256_hex = signing.calculate_sha256_bytes(req.data)
        duplicate = self._repo.get_by_sha256(model, sha256_hex, req.owner_id)
        if duplicate is not None:
            # A device reports the FIRMWARE_VERSION compiled into its image, so
            # the same bytes under two versions leaves it re-reporting the old
            # one and reflashing on every check.
            #
            # A fast path, not the guarantee. This reads before it writes, so
            # two concurrent uploads both pass it; `repo.add` raises the same
            # error off the unique index for the pair that gets through. Kept
            # because it answers before verifying and storing a blob.
            raise FirmwareBinaryAlreadyExists(model, duplicate.version)

        # Verified before storing, so a signature that does not check out
        # leaves nothing on disk. The manifest is rebuilt here out of what the
        # image and the hash say rather than out of anything the form sent: a
        # signature is only worth checking against the values it is about to be
        # stored under.
        if not req.owner_public_key:
            raise NoPublicKey(
                "This account has no public key, so an upload cannot be verified. "
                "Set one before publishing."
            )
        signing.verify_manifest(model, version, sha256_hex, req.signature, req.owner_public_key)

        # Named after its contents, so a collision implies identical bytes and
        # the overwrite in `put` is harmless by construction.
        filename = f"{sha256_hex}.bin"
        self._storage.put(filename, req.data)

        # A rejected `add` leaves the blob in place: it is shared by every row
        # with these bytes and nothing here can prove it unreferenced. The two
        # failures are not symmetric. An orphan blob wastes disk, while a row
        # whose blob was deleted under it 404s and reboot-loops every device
        # mid-download.
        return self._repo.add(
            Firmware(
                owner_id=req.owner_id,
                # Minted here rather than by the database, because it is a
                # credential and not a key: it has to be random, and a column
                # default that produced one value per statement would hand two
                # rows inserted together the same link.
                download_id=new_download_id(),
                model=model,
                version=version,
                filename=filename,
                original_filename=req.original_filename,
                signature=req.signature,
                sha256=sha256_hex,
                size_bytes=len(req.data),
                # Absent and blank collapse to null, so the dashboard has one
                # empty case to render rather than two that look the same.
                notes=(req.notes or "").strip() or None,
            )
        )
