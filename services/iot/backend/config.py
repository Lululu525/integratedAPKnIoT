"""Runtime configuration.

Reads every adjustable path and secret from environment variables, with
local-dev defaults. The SQLite database and uploaded firmware live under
`backend/data/`.

There is no signing key here. Firmware is signed by whoever built it and the
server only verifies, against the public key on the uploading account, so
`backend/keys/` now holds nothing the server reads. `scripts/generate_keys.py`
still writes a pair there because that is a convenient place for an uploader to
keep one, and `KEYS_DIR` still says where.
"""

from __future__ import annotations

import os
from functools import cached_property, lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load a local `.env` (repo root or backend/) into the environment before any
# setting is read. Real values live there; the repo only ships `.env.example`.
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent


def _require_env(name: str) -> str:
    """Read a mandatory secret from the environment, failing loudly if unset."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Copy backend/.env.example to backend/.env.")
    return value


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("DATA_DIR", BACKEND_DIR / "data"))
        self.firmware_dir = Path(os.environ.get("FIRMWARE_DIR", self.data_dir / "firmware"))
        self.db_path = Path(os.environ.get("DB_PATH", self.data_dir / "app.db"))
        # Resolved here with everything else. As a property re-reading the
        # environment it could name a different database on each access than
        # `db_path` does, and the two are used together.
        self.database_url = os.environ.get("DATABASE_URL", f"sqlite:///{self.db_path}")

        # Where `scripts/generate_keys.py` writes an uploader's pair. Read by
        # the scripts, never by the server.
        self.keys_dir = Path(os.environ.get("KEYS_DIR", BACKEND_DIR / "keys"))
        self.private_key_path = Path(
            os.environ.get("PRIVATE_KEY_PATH", self.keys_dir / "private_key.pem")
        )
        self.public_key_path = Path(
            os.environ.get("PUBLIC_KEY_PATH", self.keys_dir / "public_key.pem")
        )

        self.jwt_expires_minutes = int(os.environ.get("JWT_EXPIRES_MINUTES", "60"))
        # How long a session can go on being renewed. The access token above is
        # what every request carries and stays short; this is the ceiling on
        # the handle that replaces it, so a browser left open renews silently
        # for a fortnight and then has to log in again.
        self.refresh_expires_days = int(os.environ.get("REFRESH_EXPIRES_DAYS", "14"))

    @cached_property
    def jwt_secret(self) -> str:
        # No fallback secret on purpose: a hardcoded default is exactly the
        # shared-admin-key mistake M2 removes. Read lazily so key generation,
        # TLS certs and alembic run before .env exists; anything touching auth
        # still fails loudly. The server checks it at boot in main.py.
        secret = _require_env("JWT_SECRET")
        # HS256 needs a 256-bit key (RFC 7518); anything shorter makes admin
        # tokens brute-forceable, so refuse to run rather than warn.
        if len(secret.encode("utf-8")) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 bytes. See backend/.env.example.")
        return secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
