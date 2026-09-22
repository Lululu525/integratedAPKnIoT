"""Create a dashboard account from the command line.

    uv run python backend/scripts/create_user.py --email bob@example.com
    uv run python backend/scripts/create_user.py --email bob@example.com \
        --public-key backend/keys/public_key.pem

For seeding: a fresh install, a test fixture and a CI run all want an account
without driving a browser, and `frontend/e2e/backend.sh` is built on it.

It goes through the same `UserManager.create` the register route uses, so
anything that becomes an account passes the same credential checks.

The password is read interactively (or from the OTA_USER_PASSWORD env var for
non-interactive use) so it never lands in shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.auth import UserCreate, UserManager  # noqa: E402
from application.session import Session  # noqa: E402
from config import get_settings  # noqa: E402
from domain.signing import InvalidPublicKey, load_public_key  # noqa: E402
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists  # noqa: E402
from infrastructure.db import SessionLocal  # noqa: E402
from infrastructure.sqlite_repo import (  # noqa: E402
    SqliteRefreshTokenRepository,
    SqliteUserRepository,
)
from infrastructure.user_db import SyncUserDatabase  # noqa: E402
from pydantic import ValidationError  # noqa: E402


async def _create(email: str, password: str, public_key: str | None) -> str:
    settings = get_settings()
    db = SessionLocal()
    try:
        users = SqliteUserRepository(db)
        session = Session(SqliteRefreshTokenRepository(db), users, settings.refresh_expires_days)
        manager = UserManager(SyncUserDatabase(users), session, settings.jwt_secret)
        user = await manager.create(UserCreate(email=email, password=password))
        if public_key is not None:
            # Through the same normalizer the route uses, so a key seeded here
            # is stored in the form an upload will be verified against.
            user_db = SyncUserDatabase(users)
            user = await user_db.update(user, {"public_key": load_public_key(public_key)})
        return f"Created '{user.email}' (id={user.id})."
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a dashboard user.")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--public-key",
        type=Path,
        help="PEM file whose key this account's uploads are verified against",
    )
    args = parser.parse_args()

    password = os.environ.get("OTA_USER_PASSWORD") or getpass.getpass("Password: ")

    public_key = None
    if args.public_key is not None:
        try:
            public_key = args.public_key.read_text()
        except OSError as exc:
            print(f"Cannot read {args.public_key}: {exc}", file=sys.stderr)
            return 1

    try:
        print(asyncio.run(_create(args.email, password, public_key)))
    except InvalidPublicKey as exc:
        print(f"{args.public_key} is not usable: {exc}", file=sys.stderr)
        return 1
    except ValidationError:
        print(f"'{args.email}' is not a valid email address.", file=sys.stderr)
        return 1
    except InvalidPasswordException as exc:
        print(str(exc.reason).capitalize() + ".", file=sys.stderr)
        return 1
    except UserAlreadyExists:
        print(f"An account for '{args.email}' already exists.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
