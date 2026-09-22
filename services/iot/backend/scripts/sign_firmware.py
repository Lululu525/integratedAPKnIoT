"""Sign a firmware image so the server will accept it.

    uv run python backend/scripts/sign_firmware.py esp32/main/build/main.ino.bin

Reads the image, takes the model and version out of its build marker, hashes
the exact bytes, builds the `model|version|sha256` manifest and prints the
base64 RSA-PSS signature to paste into the upload form.

Consuming the build output directly is the point of it existing. The manifest
covers a hash of the whole file, so a signature computed against the wrong copy
of an image is a signature nothing rejects until a device has downloaded it,
failed verification and given up. Assembling that string by hand is one
transposed character away from exactly that.

The private key stays on this machine. The server holds only the matching
public key, which is what makes the signature say who built the image rather
than who uploaded it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402
from domain import signing  # noqa: E402
from domain.firmware_image import InvalidFirmwareImage, read_build_tag, validate_image  # noqa: E402


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Sign a firmware image for upload.")
    parser.add_argument("image", type=Path, help="path to the .bin to publish")
    parser.add_argument(
        "--private-key",
        type=Path,
        default=settings.private_key_path,
        help="signing key (default: the one generate_keys.py wrote)",
    )
    parser.add_argument("--model", help="override, for an image with no build marker")
    parser.add_argument("--version", help="override, for an image with no build marker")
    args = parser.parse_args()

    try:
        data = args.image.read_bytes()
    except OSError as exc:
        print(f"Cannot read {args.image}: {exc}", file=sys.stderr)
        return 1

    # The same structural check the server runs, so a file that would be
    # refused on upload is refused here instead of after a paste.
    try:
        validate_image(data)
    except InvalidFirmwareImage as exc:
        print(str(exc), file=sys.stderr)
        return 1

    tag = read_build_tag(data)
    model = args.model or (tag.model if tag else None)
    version = args.version or (tag.version if tag else None)
    if not model or not version:
        print(
            "This image carries no build marker, so --model and --version are required.",
            file=sys.stderr,
        )
        return 1
    if tag and (
        (args.model and args.model != tag.model) or (args.version and args.version != tag.version)
    ):
        # The same refusal the server makes, for the same reason: signing what
        # the operator typed over what the image says produces a signature the
        # server then rejects, and the message it gives is about the mismatch
        # rather than about the key.
        print(
            f"Image says {tag.model} {tag.version}, "
            f"you asked for {args.model or model} {args.version or version}.",
            file=sys.stderr,
        )
        return 1

    try:
        private_key_pem = args.private_key.read_bytes()
    except OSError as exc:
        print(
            f"Cannot read the signing key at {args.private_key}: {exc}\n"
            "Generate one with: uv run python backend/scripts/generate_keys.py",
            file=sys.stderr,
        )
        return 1

    try:
        signing.validate_manifest_fields(model, version)
    except signing.InvalidManifestField as exc:
        print(str(exc), file=sys.stderr)
        return 1

    sha256_hex = signing.calculate_sha256_bytes(data)
    signature = signing.sign_manifest(model, version, sha256_hex, private_key_pem)

    print(f"model:     {model}")
    print(f"version:   {version}")
    print(f"sha256:    {sha256_hex}")
    print(f"manifest:  {signing.build_manifest(model, version, sha256_hex)}")
    print()
    print(signature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
