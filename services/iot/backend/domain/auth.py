"""The rules an account's credentials must satisfy.

Hashing and token minting belong to fastapi-users. What is left is the part no
library can decide for this project, kept in the domain so both doors onto
account creation reach the same answer: `scripts/create_user.py`, which seeds
a fresh install, and `POST /api/auth/register`. Both go through `UserManager`,
which calls these.
"""

from __future__ import annotations

MIN_PASSWORD_LENGTH = 8

# Argon2 is memory-hard by design, so its cost scales with what it is handed.
# Unbounded input on an unauthenticated route is therefore a way to spend the
# server's RAM, not just its CPU. The ceiling is far above any real password.
MAX_PASSWORD_BYTES = 1024


class InvalidCredentialFormat(ValueError):
    """A credential no account may be created from."""


def normalize_email(email: str) -> str:
    """Return the form an address is both stored and looked up under.

    Lowercased, because the two halves of an address do not agree on case:
    domains are case-insensitive and the local part is technically not, but no
    mail provider in practice delivers `Foo@x.com` and `foo@x.com` to different
    people. Storing both as separate accounts is a support ticket, so the
    server picks one reading and applies it on write and on read alike.

    Whether the address is well-formed at all is Pydantic's `EmailStr`, on the
    schema. This only settles the form.
    """
    return email.strip().lower()


def validate_password(password: str) -> None:
    """Reject a password no account may be created from."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidCredentialFormat(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise InvalidCredentialFormat(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
