from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from domain.signing import (
    InvalidManifestField,
    build_manifest,
    compare_version,
    parse_version,
    sign_manifest,
    validate_manifest_fields,
)


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_key, private_pem


def test_build_manifest_joins_with_pipes():
    assert build_manifest("ESP32", "1.0.1", "abcd") == "ESP32|1.0.1|abcd"


def test_sign_manifest_produces_verifiable_signature(keypair):
    private_key, private_pem = keypair
    model, version, sha256_hex = "ESP32", "1.0.1", "a" * 64

    signature_b64 = sign_manifest(model, version, sha256_hex, private_pem)
    signature = base64.b64decode(signature_b64)

    manifest_bytes = build_manifest(model, version, sha256_hex).encode("utf-8")
    private_key.public_key().verify(
        signature,
        manifest_bytes,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )


def test_sign_manifest_rejects_tampered_manifest(keypair):
    private_key, private_pem = keypair
    signature_b64 = sign_manifest("ESP32", "1.0.1", "a" * 64, private_pem)
    signature = base64.b64decode(signature_b64)

    tampered = build_manifest("ESP32", "1.0.2", "a" * 64).encode("utf-8")
    with pytest.raises(InvalidSignature):
        private_key.public_key().verify(
            signature,
            tampered,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("1.0.1", "1.0.0", True),
        ("1.0.0", "1.0.1", False),
        ("1.0.0", "1.0.0", False),
        ("1.2.10", "1.2.9", True),
        ("2.0.0", "1.9.9", True),
        ("1.9.9", "2.0.0", False),
    ],
)
def test_compare_version(latest, current, expected):
    assert compare_version(latest, current) is expected


def test_compare_version_shorter_segment_list_is_not_newer():
    # split(".", 2) drops trailing segments beyond the third, so a shorter
    # dotted version is a tuple-prefix of a longer one and compares as older.
    assert compare_version("1.2", "1.2.0") is False


@pytest.mark.parametrize(
    "version,expected",
    [
        ("1.2.3", (1, 2, 3)),
        # Only three segments; the device caps at three the same way.
        ("1.2.3.4", (1, 2, 3)),
        # Leading digits are kept, the rest dropped, matching String::toInt.
        ("1.0.0-rc1", (1, 0, 0)),
        # A wholly non-numeric segment degrades to 0 rather than raising.
        ("1.x.3", (1, 0, 3)),
        ("abc", (0,)),
        # Parsing stops at the first empty segment.
        ("1..3", (1,)),
        ("", ()),
    ],
)
def test_parse_version(version, expected):
    assert parse_version(version) == expected


def test_compare_version_ignores_a_fourth_segment():
    # Both collapse to (1, 2, 3), because the device only ever reads three
    # segments. Making Python read a fourth would break that parity.
    assert compare_version("1.2.3.5", "1.2.3.4") is False


def test_compare_version_tolerates_malformed_without_raising():
    # A bad record must never crash an update check; it just compares as 0s.
    assert compare_version("1.0.1", "1.0.x") is True
    assert compare_version("garbage", "1.0.0") is False


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3", "10.20.30", "2026.8.10"])
def test_validate_manifest_fields_accepts_three_numeric_segments(version):
    validate_manifest_fields("ESP32-S3-DevKit", version)


@pytest.mark.parametrize(
    "version",
    [
        "v2.0.0",
        "2.0.0-rc1",
        "1.2",
        "1.2.3.4",
        "1.2.x",
        "",
        " 1.2.3",
        "1.2.3 ",
        # Unicode decimal digits: `int()` reads these as 1.0.0 while the
        # device's `String::toInt()` on the same bytes reads 0.0.0.
        "１.０.０",
    ],
)
def test_validate_manifest_fields_rejects_anything_else(version):
    # Every one of these parses to a tuple the uploader did not intend, and the
    # parser is required to stay lenient, so this is the only place to say no.
    with pytest.raises(InvalidManifestField):
        validate_manifest_fields("ESP32", version)


@pytest.mark.parametrize("model", ["", "  ", " ESP32", "ESP32 ", "ESP32|9.9.9", "ESP\n32"])
def test_validate_manifest_fields_rejects_unusable_models(model):
    with pytest.raises(InvalidManifestField):
        validate_manifest_fields(model, "1.0.0")


def test_a_model_carrying_the_separator_would_move_the_field_boundary():
    # Not an attack (upload is admin-gated and the device supplies its own
    # model), but the manifest has no escaping, so the split has to be
    # unambiguous by construction.
    assert build_manifest("ESP32|9.9.9", "1.0.0", "ab") == build_manifest(
        "ESP32", "9.9.9|1.0.0", "ab"
    )
