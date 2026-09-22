"""Manifest building, signature verification, hashing and version-compare.

The firmware manifest is signed with RSA-PSS so an ESP32 can verify a download
against its embedded public key. The manifest format and PSS parameters must
stay in step with the on-device verifier in `esp32/main/ota.cpp`; changing
either means re-flashing every device.

The server verifies and does not sign. `sign_manifest` is still here because
`scripts/sign_firmware.py` is the other half of the same contract and has to
build the identical string, but nothing on the request path calls it: the
private key belongs to whoever built the image, and a server that cannot
produce a signature is a server whose compromise cannot produce firmware any
device will accept.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)


class InvalidManifestField(ValueError):
    """A model or version that must not reach a signed manifest."""


def calculate_sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of an in-memory byte string."""
    return hashlib.sha256(data).hexdigest()


def build_manifest(model: str, version: str, sha256_hex: str) -> str:
    """The exact string the device re-builds and verifies: `model|version|sha256`."""
    return f"{model}|{version}|{sha256_hex}"


# `[0-9]`, not `\d`, which also matches Unicode decimal digits. `int()` accepts
# those and would build a real tuple here while `String::toInt()` on the same
# UTF-8 bytes returns 0, which is the divergence this check exists to close.
VERSION_FORMAT = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

# Below this an RSA key is not slow to forge, it is cheap. The device has no
# opinion, so the floor has to be here.
MIN_PUBLIC_KEY_BITS = 2048


def validate_manifest_fields(model: str, version: str) -> None:
    """Reject a model or version the manifest cannot carry unambiguously.

    Strictness belongs here rather than in `parse_version`, which has to stay
    lenient to match `String::toInt()` in `esp32/main/ota.cpp`. Tightening the
    parser instead would put the two sides out of step on every deployed
    device; guarding the one place a new value enters the system does not.

    Three numeric segments, always. Anything else parses to something the
    uploader did not mean and nothing reports it: `v2.0.0` reads as `(0, 0, 0)`
    and loses to every real release, `1.2.3.4` truncates to `1.2.3`.

    `|` separates the manifest's fields and nothing escapes it, so a field
    carrying one moves the boundary the device splits on.
    """
    if not model or model.strip() != model:
        raise InvalidManifestField("model must not be empty or padded with whitespace")
    if any(not c.isprintable() for c in model):
        raise InvalidManifestField("model must not contain control characters")
    if "|" in model:
        raise InvalidManifestField("model must not contain '|', the manifest field separator")
    if not VERSION_FORMAT.fullmatch(version):
        raise InvalidManifestField(f"version must look like 1.2.3, got {version!r}")


def sign_manifest(model: str, version: str, sha256_hex: str, private_key_pem: bytes) -> str:
    """Sign a manifest with RSA-PSS (MGF1-SHA256, max salt) and return base64.

    Matches the on-device verifier in `esp32/main/ota.cpp` which sets
    `MBEDTLS_RSA_PKCS_V21` with SHA-256.
    """
    manifest_bytes = build_manifest(model, version, sha256_hex).encode("utf-8")
    private_key = load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        manifest_bytes,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


class InvalidPublicKey(ValueError):
    """A stored or submitted public key that cannot be used to verify anything."""


class SignatureRejected(Exception):
    """The signature does not cover this manifest under this account's key."""


def verify_manifest(
    model: str, version: str, sha256_hex: str, signature_b64: str, public_key_pem: str
) -> None:
    """Check a signature over `model|version|sha256`. Raises on anything but a match.

    The salt length is `AUTO` rather than `MAX_LENGTH`, which is what the
    device does: mbedtls recovers the salt length from the encoded message and
    accepts any conforming signer. Pinning the maximum here would refuse
    signatures the ESP32 would go on to accept, which puts the two verifiers
    out of step in the one direction that is confusing rather than safe: the
    upload is rejected and the image it describes would have flashed fine.
    """
    try:
        public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise InvalidPublicKey(str(exc)) from exc
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise InvalidPublicKey("public key must be RSA")

    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SignatureRejected("signature is not valid base64") from exc

    manifest_bytes = build_manifest(model, version, sha256_hex).encode("utf-8")
    try:
        public_key.verify(
            signature,
            manifest_bytes,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise SignatureRejected(
            f"signature does not cover {build_manifest(model, version, sha256_hex)}"
        ) from exc


def load_public_key(public_key_pem: str) -> str:
    """Return the PEM an account should store, or raise `InvalidPublicKey`.

    Re-serialized rather than stored as typed. A key that round-trips is a key
    that will load again at upload time, and normalizing the encoding means a
    stray blank line or CRLF is settled once here rather than on every verify.
    """
    try:
        public_key = load_pem_public_key(public_key_pem.strip().encode("utf-8"))
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise InvalidPublicKey("not a PEM public key") from exc
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise InvalidPublicKey("public key must be RSA")
    if public_key.key_size < MIN_PUBLIC_KEY_BITS:
        raise InvalidPublicKey(f"RSA key must be at least {MIN_PUBLIC_KEY_BITS} bits")
    return public_key.public_bytes(
        encoding=Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")


def parse_version(version: str) -> tuple[int, ...]:
    """Parse a dotted version into a comparable tuple, mirroring the device.

    Matches parseVersionSegments/isVersionNewer in esp32/main/ota.cpp. At most
    three segments (so 1.2.3.4 truncates to 1.2.3 rather than differing from
    the device), and parsing stops at the first empty segment.

    A non-numeric or malformed segment becomes 0 instead of raising, so one bad
    record can't crash an update check. Uploads are held to `VERSION_FORMAT`, so
    that leniency only ever covers what a device reports about itself.
    """
    segments: list[int] = []
    for part in version.split(".", 2):
        if not part:
            break
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        segments.append(int(digits) if digits else 0)
    return tuple(segments)


def compare_version(latest_v: str, current_v: str) -> bool:
    """Return True if `latest_v` is strictly newer than `current_v`."""
    return parse_version(latest_v) > parse_version(current_v)
