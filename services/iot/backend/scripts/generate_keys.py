"""Generate the RSA key pair an uploader signs firmware with.

    uv run python backend/scripts/generate_keys.py

Writes a 2048-bit private key and its public key under `backend/keys/`, unless
they already exist. This is a key pair for whoever publishes firmware, not for
the server: the server holds no private key at all, and verifies uploads
against the public half stored on the account.

Two places the public key goes, and both are needed:

The account holds it, through the dashboard or `create_user.py --public-key`,
which is what lets an upload be accepted. Each device's `config.json` holds it
too, which is what lets the download be trusted once it lands.

The private key stays here. Losing it means publishing nothing more under this
key; leaking it means somebody else can publish firmware the fleet accepts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402


def main() -> int:
    settings = get_settings()
    settings.keys_dir.mkdir(parents=True, exist_ok=True)

    if settings.private_key_path.exists() and settings.public_key_path.exists():
        print(f"Keys already exist in {settings.keys_dir}; nothing to do.")
        return 0

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    settings.private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    settings.public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    print(f"Wrote {settings.private_key_path}")
    print(f"Wrote {settings.public_key_path}")
    print("\nPut the public key on your account and in each device's config.json.")
    print("Sign an image with: uv run python backend/scripts/sign_firmware.py <image.bin>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
